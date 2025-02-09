#!/usr/bin/env python3
import asyncio
import json
import os
import platform
import shutil
import signal
import zipfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Generator, TypedDict, cast
import aiogithubapi.github
import markdown2
import re

# from PySide6.QtWebEngineCore import QWebEnginePage
import aiohttp

from aiogithubapi import AIOGitHubAPIRatelimitException, GitHubAPI, GitHubException, GitHubReleaseAssetModel

from PySide6 import QtCore, QtWidgets
# from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtGui import QAction, QTextDocument, QTextObject, QWindow, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QErrorMessage,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)
def log(a):
    # print(a)
    pass

from qasync import QEventLoop



class InvalidSourceError(ValueError):
    pass


class SourceIOError(IOError):
    pass


class SourceReadError(SourceIOError):
    pass

class SourceWriteError(SourceIOError):
    pass


class InvalidCacheError(ValueError):
    pass


class CacheIOError(IOError):
    pass


class CacheReadError(CacheIOError):
    pass


class CacheWriteError(CacheIOError):
    pass


class DuplicateSourceEntryError(ValueError):
    pass


class SourceFetchError(IOError):
    pass


class FrayToolsAssetType(Enum):
    Plugin = "plugin"
    Template = "template"


def _is_root(info: zipfile.ZipInfo) -> bool:
    if info.is_dir():
        parts = info.filename.split("/")
        # Handle directory names with and without trailing slashes.
        if len(parts) == 1 or (len(parts) == 2 and parts[1] == ""):
            return True
    return False


def _members_without_root(archive: zipfile.ZipFile, root_filename: str) -> Generator:
    for info in archive.infolist():
        parts = info.filename.split(root_filename)
        if len(parts) > 1 and parts[1]:
            # We join using the root filename, because there might be a subdirectory with the same name.
            info.filename = root_filename.join(parts[1:])
            yield info


def extract_zip_without_root(archive_name: str, path: str):
    with zipfile.ZipFile(f"{archive_name}", mode="r") as archive:
        # We will use the first directory with no more than one path segment as the root.
        root = next(info for info in archive.infolist() if _is_root(info))

        top_level_dirs = set(info.split("/")[0] for info in archive.namelist())
        if root and len(top_level_dirs) == 1:
            archive.extractall(
                path=path, members=_members_without_root(archive, root.filename)
            )
        else:
            archive.extractall(path)


def plugin_directory() -> Path:
    dir = Path.home().joinpath("FrayToolsData", "plugins")
    if not dir.exists():
        os.makedirs(dir)
    return dir


def template_directory() -> Path:
    dir = Path.home().joinpath("FrayToolsData", "templates")
    if not dir.exists():
        os.makedirs(dir)
    return dir


def app_directory() -> Path:
    dir: Path
    if platform.system() == "Windows":
        dir = Path.home().joinpath("FrayToolsManager")
    else:
        dir = Path.home().joinpath(".config", "FrayToolsManager")
    if not dir.exists():
        os.makedirs(dir)
    if not dir.joinpath("cache").exists():
        os.makedirs(dir.joinpath("cache"))
    return dir


def cache_directory() -> Path:
    return app_directory().joinpath("cache")


def download_location(id: str, asset_type: FrayToolsAssetType) -> Path:
    match asset_type:
        case FrayToolsAssetType.Plugin:
            return cache_directory().joinpath("plugins", f"{id}")
        case FrayToolsAssetType.Template:
            return cache_directory().joinpath("templates", f"{id}")


def download_location_file(id: str, tag: str, asset_type: FrayToolsAssetType) -> Path:
    return download_location(id, asset_type).joinpath(f"{id}-{tag}.zip")


class AssetConfig:
    def __init__(self, owner: str, repo: str, id: str):
        self.owner: str = owner
        self.repo: str = repo
        self.id: str = id


class SourcesConfig:
    def __init__(self, plugins: list[AssetConfig], templates: list[AssetConfig]):
        self.plugins: list[AssetConfig] = plugins
        self.templates: list[AssetConfig] = templates

    @staticmethod
    def from_config(path: str):
        """
        Reads the sources.json
        """
        plugins: list[AssetConfig] = []
        templates: list[AssetConfig] = []

        with open(path) as config_data:
            config = json.load(config_data)
            for entry in config["plugins"]:
                plugin_config = AssetConfig(entry["owner"], entry["repo"], entry["id"])
                plugins.append(plugin_config)
            for entry in config["templates"]:
                template_config = AssetConfig(
                    entry["owner"], entry["repo"], entry["id"]
                )
                templates.append(template_config)
            parsed_config = SourcesConfig(plugins, templates)
            if parsed_config.contains_duplicates():
                raise InvalidSourceError("Duplicate config entries")
            else:
                return parsed_config

    def contains_duplicates(self):
        """
        Confirm if the source configuration contains duplicates
        """
        plugin_repos = set()
        plugin_ids = set()
        for asset in self.plugins:
            if (asset.owner, asset.repo) not in plugin_repos:
                plugin_repos.add((asset.owner, asset.repo))
            else:
                return True
            if asset.id not in plugin_ids:
                plugin_repos.add((asset.owner, asset.repo))
            else:
                return True

        template_repos = set()
        template_ids = set()
        for asset in self.templates:
            if (asset.owner, asset.repo) not in template_repos:
                template_repos.add((asset.owner, asset.repo))
            else:
                return True
            if asset.id not in template_ids:
                template_repos.add((asset.owner, asset.repo))
            else:
                return True

        return False

    def generate_asset_map(
        self, assets: list[AssetConfig]
    ) -> dict[str, dict[str, str]]:
        asset_map: dict[str, dict[str, str]] = dict()
        for template in assets:
            entry: dict[str, str] = dict()
            entry["id"] = template.id
            entry["owner"] = template.owner
            entry["repo"] = template.repo
            asset_map[template.id] = entry
        return asset_map

    def generate_asset_list(self, assets: list[AssetConfig]) -> list[dict[str, str]]:
        asset_list: list[dict[str, str]] = []
        for asset in assets:
            entry: dict[str, str] = dict()
            entry["id"] = asset.id
            entry["owner"] = asset.owner
            entry["repo"] = asset.repo
            asset_list.append(entry)
        return asset_list

    def generate_map(self) -> dict:
        source_map = dict()
        source_map["plugins"] = self.generate_asset_list(self.plugins)
        source_map["templates"] = self.generate_asset_list(self.templates)
        return source_map

    def write_config(self) -> None:
        config_text: str = json.dumps(self.generate_map(), indent=2)
        try:
            with open(str(app_directory().joinpath("sources.json")), "w") as f:
                f.write(config_text)
        except IOError as e:
            raise e

    @staticmethod
    def generate_default_config():
        """
        Generates the default source configuration, containing essentials
        """
        return SourcesConfig(
            plugins=[
                AssetConfig(
                    owner="Fraymakers",
                    repo="metadata-plugin",
                    id="com.fraymakers.FraymakersMetadata",
                ),
                AssetConfig(
                    owner="Fraymakers",
                    repo="api-types-plugin",
                    id="com.fraymakers.FraymakersTypes",
                ),
                AssetConfig(
                    owner="Fraymakers",
                    repo="content-exporter-plugin",
                    id="com.fraymakers.ContentExporter",
                ),
            ],
            templates=[
                AssetConfig(
                    owner="Fraymakers",
                    repo="character-template",
                    id="charactertemplate",
                ),
                AssetConfig(
                    owner="Fraymakers", repo="assist-template", id="assisttemplate"
                ),
                AssetConfig(
                    owner="Fraymakers", repo="stage-template", id="stagetemplate"
                ),
                AssetConfig(
                    owner="Fraymakers", repo="music-template", id="musictemplate"
                ),
            ],
        )

    def add_entry(self,owner: str, repo: str, id: str, asset_type: FrayToolsAssetType):
        match asset_type:
            case FrayToolsAssetType.Plugin:
                for plugin in self.plugins:
                    if (owner, repo) == (plugin.owner, plugin.repo):
                        raise DuplicateSourceEntryError(
                            "Cannot add plugin source with the same repository"
                        )
                    if id == plugin.id:
                        raise DuplicateSourceEntryError(
                            "Cannot add plugin source with conflicting id"
                        )
                self.plugins.append(AssetConfig(owner=owner, repo=repo, id=id))
            case FrayToolsAssetType.Template:
                for template in self.templates:
                    if (owner, repo) == (template.owner, template.repo):
                        raise DuplicateSourceEntryError(
                            "Cannot add template source with same repository"
                        )
                    if id == template.id:
                        raise DuplicateSourceEntryError(
                            "Cannot add template source with conflicting id"
                        )
                self.templates.append(AssetConfig(owner=owner, repo=repo, id=id))
        self.write_config()

    def edit_entry(self,index:int , id: str, owner:str, repo:str, asset_type: FrayToolsAssetType):
        match asset_type:
            case FrayToolsAssetType.Plugin:
                for idx, plugin in enumerate(self.plugins):
                    if id == plugin.id:
                        self.plugins[idx] = AssetConfig(owner=owner, repo=repo, id=id)
            case FrayToolsAssetType.Template:
                for idx, template in enumerate(self.templates):
                    if id == template.id:
                        self.templates[idx] = AssetConfig(owner=owner, repo=repo, id=id)
        self.write_config()

    def remove_entry(self, id: str, asset_type: FrayToolsAssetType):
        match asset_type:
            case FrayToolsAssetType.Plugin:
                self.plugins = list(filter(lambda a: a.id != id, self.plugins))
            case FrayToolsAssetType.Template:
                self.templates = list(filter(lambda a: a.id != id, self.templates))

    def index(self, asset_config:AssetConfig, asset_type: FrayToolsAssetType) -> int:
        match asset_type:
            case FrayToolsAssetType.Plugin:
                return self.plugins.index(asset_config)
            case FrayToolsAssetType.Template:
                return self.templates.index(asset_config)


@dataclass
class TemplateManifest:
    id: str
    path: str


@dataclass
class PluginManifest:
    name: str
    plugin_type: str
    id: str
    version: str
    description: str
    path: str


@dataclass
class FrayToolsAssetVersion:
    url: str
    tag: str
    changelog: str


class FrayToolsAsset:
    def __init__(
        self,
        asset_type: FrayToolsAssetType,
        id: str,
        owner: str,
        repo: str,
        versions: list[FrayToolsAssetVersion],
    ):
        self.id = id
        self.repo = repo
        self.owner = owner
        self.versions = versions
        self.asset_type = asset_type

    @staticmethod
    async def fetch_data(
        config: AssetConfig | AssetConfig, asset_type: FrayToolsAssetType
    ):
        gh = GitHubAPI()
        id = config.id
        owner = config.owner
        repo = config.repo
        try:
            versions: list[FrayToolsAssetVersion] = []
            releases = await gh.repos.releases.list(f"{owner}/{repo}")
            if releases.data is not None:
                for release in releases.data:
                    asset_url: str
                    if release.assets and len(release.assets) > 0:
                        asset: GitHubReleaseAssetModel = release.assets[0]
                        asset_url = asset.browser_download_url
                    elif (
                        asset_type == FrayToolsAssetType.Template
                        and release.zipball_url is not None
                    ):
                        asset_url = release.zipball_url
                    else:
                        continue
                    tag = release.name
                    changelog: str = str(release.body)
                    plugin_version = FrayToolsAssetVersion(asset_url, str(tag), changelog)
                    versions.append(plugin_version)
        except GitHubException as e:
            raise SourceFetchError(f"Failed to Fetch Data for {id}: {e}")
        finally:
            await gh.close_session()

        return FrayToolsAsset(asset_type, id, owner, repo, versions)

    async def download_version(self, index: int):
        download_url = self.versions[index].url
        tag: str = self.versions[index].tag
        name: str = self.id
        log(f"Starting Download of {name}-{tag}")
        download_path: Path = download_location(name, self.asset_type)
        if not download_path.exists():
            os.makedirs(download_path)

        filename: str = str(download_location_file(name, tag, self.asset_type))
        async with aiohttp.ClientSession() as session:
            async with session.get(download_url) as response:
                with open(filename, mode="wb") as file:
                    while True:
                        chunk = await response.content.read()
                        if not chunk:
                            log(f"Finished Downloading {name}-{tag}")
                            break
                        file.write(chunk)

    def install_version(
        self,
        index: int,
        asset_type: FrayToolsAssetType,
        plugin_manifests: dict[str, PluginManifest] | None = None,
        template_manifests: dict[str, TemplateManifest] | None = None,
    ):
        log("Starting install")
        tag: str = self.versions[index].tag
        id: str = self.id
        manifests: dict[str, PluginManifest] | dict[str, TemplateManifest] = dict()
        outputdir: Path = Path()
        if asset_type == FrayToolsAssetType.Plugin and plugin_manifests is not None:
            outputdir = plugin_directory()
            manifests = plugin_manifests

        elif (
            asset_type == FrayToolsAssetType.Template and template_manifests is not None
        ):
            outputdir = template_directory()
            manifests = template_manifests

        manifest_path = None
        download_path: Path = download_location(id, asset_type)
        if not download_path.exists():
            os.makedirs(download_path)

        filename: str = str(download_location_file(id, tag, asset_type))
        if manifests is not None and self.id in manifests.keys():
            manifest_path = Path(manifests[self.id].path)

        if manifest_path is not None:
            extract_zip_without_root(filename, str(manifest_path))
            with open(manifest_path.joinpath(".fraytools-manager-version"), "w") as text_file:
                _ = text_file.write(tag)
        else:
            outpath = outputdir.joinpath(id)
            if not os.path.isdir(outpath):
                os.makedirs(outpath)
            for path in outpath.iterdir():
                if path.is_file():
                    path.unlink()
                if path.is_dir():
                    shutil.rmtree(str(path))

            extract_zip_without_root(filename, str(outpath))
            with open(outpath.joinpath(".fraytools-manager-version"), "w") as text_file:
                _ = text_file.write(tag)
        log("Completed Install")

    def get_changelog(self, index: int):
        return self.versions[index].changelog


class AssetEntry:
    def __init__(
        self,
        plugin_manifest: PluginManifest | None,
        template_manifest: TemplateManifest | None,
        config: AssetConfig | None,
        asset: FrayToolsAsset | None,
        asset_type: FrayToolsAssetType,
    ):
        self.asset = asset
        self.config = config
        self.asset_type = asset_type
        self.manifest: None | PluginManifest | TemplateManifest = None
        if asset_type == FrayToolsAssetType.Plugin and plugin_manifest is not None:
            self.manifest = plugin_manifest
        elif (
            asset_type == FrayToolsAssetType.Template and template_manifest is not None
        ):
            self.manifest = template_manifest

    def display_name(self) -> str:
        display_name: str = "unknown asset"
        if (
            self.manifest
            and self.asset_type == FrayToolsAssetType.Plugin
            and isinstance(self.manifest, PluginManifest)
        ):
            display_name = f"{self.manifest.name} ({self.manifest.id})"
        elif self.manifest:
            display_name = f"{self.manifest.id}"
        elif self.asset:
            display_name = f"{self.asset.id}"
        elif self.config:
            display_name = f"{self.config.id}"

        return display_name

    def is_installed(self, selected_version: str | None) -> bool:
        found_version = False
        if self.manifest:
            path = Path(self.manifest.path).joinpath(".fraytools-manager-version")
            try:
                if path.exists() and path.is_file():
                    with open(path, "r") as version:
                        version_string = version.read()
                        found_version = version_string == selected_version
            except IOError:
                found_version = False

        return found_version or (
            self.manifest is not None and (self.asset is None or self.config is None)
        ) or (
            self.manifest is not None
            and self.asset is not None
            and isinstance(self.manifest, PluginManifest)
            and (
                self.asset_type == FrayToolsAssetType.Plugin
                and self.manifest.version == selected_version
            )
        )

    def can_download(self, selected_version: str | None) -> bool:
        if selected_version is None or self.config is None:
            return False

        return (
            not self.is_installed(selected_version)
            and self.asset is not None
            and selected_version is not None
            and not download_location_file(
                self.asset.id, selected_version, self.asset_type
            ).exists()
        )

    def can_uninstall(self, selected_version: str | None) -> bool:
        return self.is_installed(selected_version)

    def can_install(self, selected_version: str | None) -> bool:
        if selected_version is None or self.config is None:
            return False

        return (
            download_location_file(
                self.config.id, selected_version, self.asset_type
            ).exists()
            and not self.can_download(selected_version)
            and not self.is_installed(selected_version)
        )


def detect_plugins() -> list[PluginManifest]:
    manifest_paths: list[PluginManifest] = []
    for filename in os.scandir(plugin_directory()):
        if filename.is_dir():
            for subfile in os.scandir(filename.path):
                if subfile.is_file() and subfile.name == "manifest.json":
                    manifest = PluginManifest(
                        plugin_type="",
                        description="",
                        id="",
                        version="",
                        name="",
                        path=filename.path,
                    )
                    with open(subfile.path) as manifest_data:
                        config = json.load(manifest_data)
                        manifest.name = config["name"]
                        manifest.plugin_type = config["type"]
                        manifest.id = config["id"]
                        manifest.description = config["description"]
                        manifest.version = config["version"]
                    manifest_paths.append(manifest)

    return manifest_paths


def detect_templates() -> list[TemplateManifest]:
    template_manifests: list[TemplateManifest] = []
    for filename in os.scandir(template_directory()):
        if filename.is_dir():
            template_path = Path(filename.path)
            manifest_location = template_path.joinpath("library", "manifest.json")
            if manifest_location.is_file():
                with open(manifest_location) as manifest_data:
                    config = json.load(manifest_data)
                    manifest: TemplateManifest = TemplateManifest(
                        config["resourceId"], path=str(template_path)
                    )
                    template_manifests.append(manifest)
            else:
                continue
    return template_manifests


def generate_manifest_map(
    manifests: list[PluginManifest] | list[TemplateManifest],
) -> dict:
    manifest_map: dict[str, object] = dict()
    for manifest in manifests:
        manifest_map[manifest.id] = manifest
    return manifest_map


def generate_asset_map(assets: list[FrayToolsAsset]) -> dict[str, FrayToolsAsset]:
    asset_map: dict[str, FrayToolsAsset] = dict()
    for asset in assets:
        asset_map[asset.id] = asset
    return asset_map


def generate_config_map(assets: list[AssetConfig]) -> dict[str, AssetConfig]:
    config_map: dict[str, AssetConfig] = dict()
    for asset in assets:
        config_map[asset.id] = asset
    return config_map


class CachedFrayToolsAssetVersion(TypedDict):
    url: str
    tag: str
    changelog: str


class CachedFrayToolsAsset(TypedDict):
    id: str
    owner: str
    repo: str
    versions: list[CachedFrayToolsAssetVersion]


class SourcesCache(TypedDict):
    plugins: dict[str, CachedFrayToolsAsset]
    templates: dict[str, CachedFrayToolsAsset]


sources_config: SourcesConfig
sources_cache: SourcesCache = SourcesCache(plugins=dict(), templates=dict())

template_entries: list[AssetEntry] = []
template_config_map: dict[str, AssetConfig] = dict()
template_manifest_map: dict[str, TemplateManifest] = dict()
template_map: dict[str, FrayToolsAsset] = dict()

plugin_entries: list[AssetEntry] = []
plugin_config_map: dict[str, AssetConfig] = dict()
plugin_manifest_map: dict[str, PluginManifest] = dict()
plugin_map: dict[str, FrayToolsAsset] = dict()


class Cache:
    @staticmethod
    def asset_to_cache(asset: FrayToolsAsset) -> CachedFrayToolsAsset:
        return CachedFrayToolsAsset(
            id=asset.id,
            owner=asset.owner,
            repo=asset.repo,
            versions=list(
                map(
                    lambda x: CachedFrayToolsAssetVersion(
                        url=x.url, tag=x.tag, changelog=x.changelog
                    ),
                    asset.versions,
                )
            ),
        )

    @staticmethod
    def cache_to_asset(
        asset: CachedFrayToolsAsset, asset_type: FrayToolsAssetType
    ) -> FrayToolsAsset:
        return FrayToolsAsset(
            asset_type=asset_type,
            id=asset["id"],
            owner=asset["owner"],
            repo=asset["repo"],
            versions=list(
                map(
                    lambda x: FrayToolsAssetVersion(
                        url=x["url"], tag=x["tag"], changelog=x["changelog"]
                    ),
                    asset["versions"],
                )
            ),
        )

    @staticmethod
    def clear():
        global sources_cache
        log("Clearing Cache..")
        sources_cache = SourcesCache(plugins=dict(), templates=dict())

    @staticmethod
    def delete(id: str, asset_type: FrayToolsAssetType):
        global sources_cache
        match asset_type:
            case FrayToolsAssetType.Plugin:
                sources_cache["plugins"].pop(id)
            case FrayToolsAssetType.Template:
                sources_cache["templates"].pop(id)

    @staticmethod
    def add(asset: FrayToolsAsset, asset_type: FrayToolsAssetType):
        global sources_cache
        match asset_type:
            case FrayToolsAssetType.Plugin:
                sources_cache["plugins"][asset.id] = Cache.asset_to_cache(asset)
            case FrayToolsAssetType.Template:
                sources_cache["templates"][asset.id] = Cache.asset_to_cache(asset)

    @staticmethod
    def exists(id: str, asset_type: FrayToolsAssetType):
        global sources_cache
        match asset_type:
            case FrayToolsAssetType.Plugin:
                return id in sources_cache["plugins"].keys()
            case FrayToolsAssetType.Template:
                return id in sources_cache["templates"].keys()

    @staticmethod
    def get(id: str, asset_type: FrayToolsAssetType) -> FrayToolsAsset:
        global sources_cache
        match asset_type:
            case FrayToolsAssetType.Plugin:
                return Cache.cache_to_asset(sources_cache["plugins"][id], asset_type)
            case FrayToolsAssetType.Template:
                return Cache.cache_to_asset(sources_cache["templates"][id], asset_type)

    @staticmethod
    def write_to_disk() -> None:
        global sources_cache
        json_str: str = json.dumps(sources_cache, indent=2)
        try:
            with open(cache_directory().joinpath("sources-lock.json"), "w") as f:
                log("Writing to cache on disk...")
                f.write(json_str)
                log("Successfully wrote to cache on disk")
        except IOError:
            raise CacheWriteError("Error Reading Cache")
        except ValueError:
            raise InvalidCacheError("Cache Contents Invalid")

    @staticmethod
    def read_from_disk():
        global sources_cache
        cache_file = cache_directory().joinpath("sources-lock.json")
        try:
            if cache_file.exists() and cache_file.is_file:
                with open(cache_file, "r") as f:
                    log("Reading cache from disk...")
                    sources_cache = json.loads(f.read())
                    log("Successfully read cache from disk.")
        except IOError:
            raise CacheReadError("Unable to to read cache")
        except ValueError:
            raise InvalidCacheError("Error Parsing Cache")


def generate_template_entries() -> list[AssetEntry]:
    return generate_entries(FrayToolsAssetType.Template)


def generate_plugin_entries() -> list[AssetEntry]:
    return generate_entries(FrayToolsAssetType.Plugin)


def generate_entries(asset_type: FrayToolsAssetType) -> list[AssetEntry]:
    global plugin_entries, plugin_config_map, plugin_manifest_map, plugin_map
    global template_entries, template_config_map, template_manifest_map, template_map
    config_map = dict()
    manifest_map: dict[str, TemplateManifest] | dict[str, PluginManifest] = dict()
    asset_map = dict()
    cfg_map = dict()
    match asset_type:
        case FrayToolsAssetType.Plugin:
            config_map = plugin_config_map
            asset_map = plugin_map
            manifest_map = plugin_manifest_map
            cfg_map = plugin_config_map
        case FrayToolsAssetType.Template:
            config_map = template_config_map
            asset_map = template_map
            manifest_map = template_manifest_map
            cfg_map = template_config_map
    installed_entries: list[AssetEntry] = []

    match asset_type:
        case FrayToolsAssetType.Plugin:
            tmp_plugin_manifest_map = cast(dict[str, PluginManifest], manifest_map)
            installed_entries = list(
                map(
                    lambda pmanifest: AssetEntry(
                        plugin_manifest=pmanifest,
                        template_manifest=None,
                        config=None,
                        asset=None,
                        asset_type=asset_type,
                    ),
                    tmp_plugin_manifest_map.values(),
                )
            )
        case FrayToolsAssetType.Template:
            tmp_template_manifest_map = cast(dict[str, TemplateManifest], manifest_map)
            installed_entries = list(
                map(
                    lambda tmanifest: AssetEntry(
                        plugin_manifest=None,
                        template_manifest=tmanifest,
                        config=None,
                        asset=None,
                        asset_type=asset_type,
                    ),
                    tmp_template_manifest_map.values(),
                )
            )

    uninstalled_entries: list[AssetEntry] = list(
        map(
            lambda config: AssetEntry(
                plugin_manifest=None,
                config=config,
                template_manifest=None,
                asset=None,
                asset_type=asset_type,
            ),
            (
                filter(
                    lambda p: p.id not in manifest_map.keys(),
                    cfg_map.values(),
                )
            ),
        )
    )

    for entry in installed_entries:
        if entry.manifest and entry.manifest.id in config_map.keys():
            entry.config = config_map[entry.manifest.id]
        if entry.manifest and entry.manifest.id in asset_map.keys():
            entry.asset = asset_map[entry.manifest.id]

    for entry in uninstalled_entries:
        if entry.config and entry.config.id in asset_map.keys():
            entry.asset = asset_map[entry.config.id]

    entries: list[AssetEntry] = installed_entries + uninstalled_entries
    match asset_type:
        case FrayToolsAssetType.Plugin:
            plugin_entries = entries
        case FrayToolsAssetType.Template:
            template_entries = entries

    return entries


def load_cached_asset_sources(asset_type: FrayToolsAssetType):
    global plugin_manifest_map, plugin_map, plugin_config_map, sources_cache
    global template_manifest_map, template_map, template_config_map
    global plugin_entries, template_entries
    asset_name = "Asset"
    cfg_map = dict()
    match asset_type:
        case FrayToolsAssetType.Plugin:
            plugin_config_map = generate_config_map(sources_config.plugins)
            asset_name = "Plugin"
            cfg_map = plugin_config_map
        case FrayToolsAssetType.Template:
            template_config_map = generate_config_map(sources_config.templates)
            asset_name = "Template"
            detect_fn = detect_templates
            cfg_map = template_config_map
    Cache.read_from_disk()
    log(f"Loading Cached {asset_name} Sources...")
    assets: list[FrayToolsAsset] = []
    for config in cfg_map.values():
        asset: FrayToolsAsset
        if Cache.exists(config.id, asset_type):
            asset = Cache.get(config.id, asset_type)
            log(f"Found {config.id} in cache")
            assets.append(asset)

    match asset_type:
        case FrayToolsAssetType.Plugin:
            plugin_manifest_map = generate_manifest_map(detect_plugins())
            plugin_map = generate_asset_map(assets)
        case FrayToolsAssetType.Template:
            template_manifest_map = generate_manifest_map(detect_templates())
            template_map = generate_asset_map(assets)
    plugin_entries = generate_plugin_entries()
    template_entries = generate_template_entries()
    Cache.write_to_disk()


def reload_cached_data(
    asset_type: FrayToolsAssetType | None = None, defaults: bool = False
):
    global sources_cache, sources_config
    global plugin_manifest_map, plugin_entries, plugin_map, plugin_config_map
    global template_manifest_map, template_entries, template_map, template_config_map
    if app_directory().joinpath("sources.json").exists() and not defaults:
        sources_config = SourcesConfig.from_config(
            str(app_directory().joinpath("sources.json"))
        )
    else:
        sources_config = SourcesConfig.generate_default_config()
        sources_config.write_config()
    Cache.read_from_disk()
    if asset_type is None or asset_type == FrayToolsAssetType.Plugin:
        load_cached_asset_sources(FrayToolsAssetType.Plugin)
    if asset_type is None or asset_type == FrayToolsAssetType.Template:
        load_cached_asset_sources(FrayToolsAssetType.Template)

    plugin_entries = generate_plugin_entries()
    template_entries = generate_template_entries()

    Cache.write_to_disk()


async def fetch_asset_source(id: str, asset_type: FrayToolsAssetType):
    global plugin_manifest_map, plugin_map
    global template_manifest_map, template_map
    global plugin_entries, template_entries
    asset_name = "Asset"
    detect_fn: (
        Callable[[], list[PluginManifest]] | Callable[[], list[TemplateManifest]]
    ) = lambda: []
    cfg_map = dict()
    match asset_type:
        case FrayToolsAssetType.Plugin:
            asset_name = "Plugin"
            detect_fn = detect_plugins
            cfg_map = plugin_config_map
        case FrayToolsAssetType.Template:
            asset_name = "Template"
            detect_fn = detect_templates
            cfg_map = template_config_map

    log(f"Refreshing {asset_name} Source...")
    assets: list[FrayToolsAsset] = []
    if id in cfg_map.keys():
        config = cfg_map[id]
        asset: FrayToolsAsset
        log(f"Fetching {config.id}")
        asset = await FrayToolsAsset.fetch_data(config, asset_type)
        Cache.add(asset, asset_type)
        log(f"Added {config.id} to cache")
        assets.append(asset)

    match asset_type:
        case FrayToolsAssetType.Plugin:
            plugin_manifest_map = generate_manifest_map(detect_fn())
            plugin_map = generate_asset_map(assets)
        case FrayToolsAssetType.Template:
            template_manifest_map = generate_manifest_map(detect_fn())
            template_map = generate_asset_map(assets)

    plugin_entries = generate_plugin_entries()
    template_entries = generate_template_entries()
    Cache.write_to_disk()


async def fetch_asset_sources(asset_type: FrayToolsAssetType):
    global plugin_manifest_map, plugin_map
    global template_manifest_map, template_map
    global plugin_entries, template_entries
    asset_name = "Asset"
    cfg_map = dict()
    match asset_type:
        case FrayToolsAssetType.Plugin:
            asset_name = "Plugin"
            cfg_map = plugin_config_map
        case FrayToolsAssetType.Template:
            asset_name = "Template"
            cfg_map = template_config_map

    log(f"Refreshing {asset_name} Sources...")
    for id in cfg_map.keys():
        await fetch_asset_source(id, asset_type)
    Cache.write_to_disk()


async def refresh_data_async(asset_type: FrayToolsAssetType | None = None):
    global sources_cache, sources_config
    global plugin_manifest_map, plugin_entries, plugin_map, plugin_config_map
    global template_manifest_map, template_entries, template_map, template_config_map
    reload_cached_data(asset_type)
    if asset_type is None or asset_type == FrayToolsAssetType.Plugin:
        await fetch_asset_sources(FrayToolsAssetType.Plugin)
    if asset_type is None or asset_type == FrayToolsAssetType.Template:
        await fetch_asset_sources(FrayToolsAssetType.Template)
    Cache.write_to_disk()

    plugin_entries = generate_plugin_entries()
    template_entries = generate_template_entries()


def display_error_popup(widget: QWidget, message: str):
    err = QErrorMessage(widget)
    err.setWindowTitle("Something went wrong!")
    err.showMessage(message)


def refresh_data_ui_offline(widget: QtWidgets.QWidget):
    try:
        reload_cached_data()
    except (IOError, ValueError) as e:
        display_error_popup(widget, str(e))


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.setWindowTitle("FrayTools Manager")
        self.tabs = QTabWidget(self)
        self.plugin_list = AssetListWidget(FrayToolsAssetType.Plugin, self)
        self.template_list = AssetListWidget(FrayToolsAssetType.Template, self)
        self.settings_menu = SettingsWidget(self)
        self.tabs.addTab(self.plugin_list, "Plugins")
        self.tabs.addTab(self.template_list, "Templates")
        self.tabs.addTab(self.settings_menu, "Settings")
        self.setCentralWidget(self.tabs)
        self.setMinimumSize(QtCore.QSize(800, 600))

        sources_menu = self.menuBar().addMenu("Sources")
        add_sources_action = QAction("Add Source...", self)
        add_sources_action.triggered.connect(lambda: SourceEntryDialogue(self).exec())

        self.fetch_sources_action = QAction("Refresh", self)
        self.fetch_sources_action.triggered.connect(
            lambda: asyncio.ensure_future(self.settings_menu.refresh_sources())
        )

        sources_menu.addAction(add_sources_action)
        sources_menu.addAction(self.fetch_sources_action)

        cache_menu = self.menuBar().addMenu("Cache")

        clear_sources_action = QAction("Clear Sources Cache", self)
        clear_sources_action.triggered.connect(
            lambda: self.settings_menu.clear_sources_cache()
        )

        delete_downloads_action = QAction("Delete Dowload Cache", self)
        delete_downloads_action.triggered.connect(
            lambda: self.settings_menu.clear_download_cache()
        )

        cache_menu.addAction(clear_sources_action)
        cache_menu.addAction(delete_downloads_action)
        self.reload()

    def reload(self):
        refresh_data_ui_offline(self)
        self.plugin_list.reload()
        self.template_list.reload()


class SourceEntryDialogue(QtWidgets.QDialog):
    def __init__(self, main_menu: MainWindow,existing_config:AssetConfig = None, asset_type:FrayToolsAssetType = None):
        super().__init__()
        self.edit_mode = existing_config is not None
        self.asset_type: FrayToolsAssetType = FrayToolsAssetType.Plugin
        if asset_type is not None:
            self.asset_type = asset_type
            self.index:int = sources_config.index(existing_config, self.asset_type)

        if self.edit_mode:
            self.asset_config = existing_config
            self.setWindowTitle("Edit Source Entry")
        else:
            self.asset_config: AssetConfig = AssetConfig(id="", owner="", repo="")
            self.setWindowTitle("Add Source Entry")
        self.setMinimumWidth(400)
        self.main_menu = main_menu

        self.owner_input = QLineEdit("", self)
        self.owner_input.setPlaceholderText("Github Repo Owner")
        self.owner_input.textEdited.connect(self.owner_edited)

        self.repo_input = QLineEdit("", self)
        self.repo_input.setPlaceholderText("Github Repository Name")
        self.repo_input.textEdited.connect(self.repo_edited)

        self.id_input = QLineEdit("", self)
        self.id_input.setPlaceholderText("Plugin Manifest Id")
        self.id_input.textEdited.connect(self.id_edited)

        self.asset_type_input = QComboBox(self)
        self.asset_type_input.addItems(["Plugin", "Template"])
        self.asset_type_input.currentIndexChanged.connect(self.on_select)
        self.asset_type_input.setCurrentIndex(0)

        self.add_button = QPushButton(self)
        if self.edit_mode:
            self.add_button.setText("Apply Changes")
        else:
            self.add_button.setText("Add Source")

        self.add_button.pressed.connect(self.submitted)

        self.items_layout = QtWidgets.QVBoxLayout(self)
        self.items_layout.addWidget(
            self.create_row([QLabel("Owner"), self.owner_input])
        )
        self.items_layout.addWidget(self.create_row([QLabel("Repo"), self.repo_input]))
        self.items_layout.addWidget(self.create_row([QLabel("Id"), self.id_input]))
        self.items_layout.addWidget(
            self.create_row([QLabel("Asset Type"), self.asset_type_input])
        )
        self.items_layout.addWidget(self.add_button)
        if self.asset_type == FrayToolsAssetType.Plugin:
            self.on_select(0)
        elif self.asset_type == FrayToolsAssetType.Template: 
            self.on_select(1)

        if self.edit_mode:
            self.owner_edited(self.asset_config.owner)
            self.repo_edited(self.asset_config.repo)
            self.id_edited(self.asset_config.id)
            self.asset_type_input.setDisabled(True)

        self.setLayout(self.items_layout)

    @QtCore.Slot()
    def submitted(self):
        validationError = False
        warnings = "Missing Fields:\n"
        if len(self.asset_config.owner) == 0:
            warnings += " Owner\n"
            validationError = True
        if len(self.asset_config.repo) == 0:
            warnings += "Repo\n"
            validationError = True
        if len(self.asset_config.id) == 0:
            warnings += "Id"
            validationError = True
        if validationError:
            display_error_popup(self, warnings)
            return

        try:

            if self.edit_mode:
                sources_config.edit_entry(
                    index = self.index,
                    owner=self.asset_config.owner,
                    repo=self.asset_config.repo,
                    id=self.asset_config.id,
                    asset_type=self.asset_type,
                )
            else:
                sources_config.add_entry(
                    owner=self.asset_config.owner,
                    repo=self.asset_config.repo,
                    id=self.asset_config.id,
                    asset_type=self.asset_type,
                )
            refresh_data_ui_offline(self)
            self.main_menu.reload()
            self.accept()
        except (DuplicateSourceEntryError, IOError, ValueError) as e:
            display_error_popup(self, str(e))

    @QtCore.Slot()
    def owner_edited(self, text: str):
        self.owner_input.setText(text.replace(" ", ""))
        self.asset_config.owner = text.replace(" ", "")

    @QtCore.Slot()
    def repo_edited(self, text: str):
        self.asset_config.repo = text.replace(" ", "")
        self.repo_input.setText(text.replace(" ", ""))

    @QtCore.Slot()
    def id_edited(self, text: str):
        self.asset_config.id = text
        self.id_input.setText(text)

    @QtCore.Slot()
    def on_select(self, index):
        if index == 0:
            self.asset_type = FrayToolsAssetType.Plugin
            self.id_input.setPlaceholderText("Plugin Manifest Id")
        else:
            self.asset_type = FrayToolsAssetType.Template
            self.id_input.setPlaceholderText("Template Manifest resourceId")

    def create_row(self, widgets: list[QWidget]) -> QWidget:
        base_widget = QWidget()
        row = QtWidgets.QVBoxLayout(self)
        for widget in widgets:
            row.addWidget(widget)
        base_widget.setLayout(row)
        return base_widget


class SettingsWidget(QtWidgets.QWidget):
    def __init__(self, parent: MainWindow) -> None:
        super().__init__()
        self.parent_ref = parent
        layout = QtWidgets.QVBoxLayout(self)
        self.settings_items = QListWidget()
        self.settings_items.setUniformItemSizes(True)
        self.settings_items.setSpacing(10)
        layout.addWidget(self.settings_items)
        self.setLayout(layout)
        self.clear_sources_button = QPushButton("Clear Sources Cache")
        self.clear_download_button = QPushButton("Clear Download Cache")
        self.refresh_button = QPushButton("Refresh")
        self.defaults_button = QPushButton("Restore Defaults")
        self.simple_settings_button(
            self.clear_sources_button,
            "Clears the sources cache",
            self.clear_sources_cache,
        )
        self.simple_settings_button(
            self.clear_download_button,
            "Clears the Download Cache",
            self.clear_download_cache,
        )
        self.simple_settings_button(
            self.refresh_button,
            "Updates Plugin Metadata, Subject to Github API Limits",
            lambda: asyncio.ensure_future(self.refresh_sources()),
        )

        self.simple_settings_button(
            self.defaults_button,
            "Restores All Sources to their defaults",
            self.restore_defaults,
        )

    def simple_settings_button(
        self, button: QPushButton, description: str, on_press
    ) -> None:
        widget = QWidget()
        widget.setMinimumHeight(40)
        row = QHBoxLayout()
        text = QLabel(description)
        button.pressed.connect(on_press)
        row.addWidget(text)
        row.addWidget(button)
        widget.setLayout(row)
        item = QListWidgetItem(self.settings_items)
        item.setSizeHint(row.sizeHint())
        self.settings_items.addItem(item)
        self.settings_items.setItemWidget(item, widget)

    def refresh_parent(self):
        self.parent_ref.reload()

    @QtCore.Slot()
    def restore_defaults(self) -> None:
        msgBox: QMessageBox = QMessageBox()
        msgBox.setWindowTitle("Restore Defaults")
        msgBox.setText("Are you sure you want to restore defaults")
        msgBox.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        msgBox.setDefaultButton(QMessageBox.StandardButton.No)
        if msgBox.exec() == QMessageBox.StandardButton.Yes:
            reload_cached_data(defaults=True)
            self.parent_ref.reload()

    @QtCore.Slot()
    def clear_sources_cache(self) -> None:
        msgBox: QMessageBox = QMessageBox()
        msgBox.setWindowTitle("Clear Sources Cache")
        msgBox.setText("Are you sure you want to clear the sources Cache?")
        msgBox.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        msgBox.setDefaultButton(QMessageBox.StandardButton.No)
        if msgBox.exec() == QMessageBox.StandardButton.Yes:
            Cache.clear()
            reload_cached_data()
            refresh_data_ui_offline(self)
            self.refresh_parent()
            Cache.write_to_disk()
        else:
            pass

    @QtCore.Slot()
    def clear_download_cache(self) -> None:
        msgBox: QMessageBox = QMessageBox()
        msgBox.setWindowTitle("Clear Download Cache")
        msgBox.setText("Are you sure you want to clear the download cache?")
        msgBox.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        msgBox.setDefaultButton(QMessageBox.StandardButton.No)
        if msgBox.exec() == QMessageBox.StandardButton.Yes:
            for p in cache_directory().iterdir():
                if p.is_dir():
                    shutil.rmtree(p)
            reload_cached_data()
            self.refresh_parent()
        else:
            pass

    @QtCore.Slot()
    async def refresh_sources(self) -> None:
        msgBox: QMessageBox = QMessageBox(self)
        msgBox.setWindowTitle("Refresh Sources")
        msgBox.setText(
            "Refreshing sources too often might result in hitting API limits, are you sure you want to proceed?"
        )
        msgBox.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        msgBox.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if msgBox.exec() == QMessageBox.StandardButton.Yes:
            try:
                self.refresh_button.setEnabled(False)
                self.refresh_button.setText("Refreshing...")
                self.parent_ref.fetch_sources_action.setEnabled(False)
                self.parent_ref.fetch_sources_action.setText("Refreshing..")
                await refresh_data_async()
            except AIOGitHubAPIRatelimitException:
                display_error_popup(self, "You've hit the Github API Rate Limit!")
            except (
                CacheWriteError,
                CacheReadError,
                InvalidCacheError,
                InvalidSourceError,
                SourceFetchError,
                IOError,
            ) as e:
                display_error_popup(self, str(e))
            finally:
                self.refresh_button.setEnabled(True)
                self.refresh_button.setText("Refresh")
                self.parent_ref.fetch_sources_action.setEnabled(True)
                self.parent_ref.fetch_sources_action.setText("Refresh")
                self.refresh_parent()

        else:
            pass


class AssetListWidget(QtWidgets.QWidget):
    def __init__(self, asset_type: FrayToolsAssetType, parent: MainWindow):
        super().__init__()
        self.parent_ref = parent
        self.asset_type = asset_type
        self.create_asset_list()
        self.add_installed_assets()

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.installed_items)
        self.setLayout(layout)

    def create_asset_list(self):
        self.installed_items = QListWidget()

    def add_installed_assets(self):
        global plugin_entries, template_entries
        self.installed_items.clear()
        entries = []
        match self.asset_type:
            case FrayToolsAssetType.Plugin:
                entries = plugin_entries
            case FrayToolsAssetType.Template:
                entries = template_entries

        for entry in entries:
            item = QListWidgetItem(self.installed_items)
            row = AssetItemWidget(entry, self.asset_type, self)
            self.installed_items.addItem(item)
            self.installed_items.setItemWidget(item, row)
            item.setSizeHint(row.minimumSizeHint())

    def reload(self):
        while self.installed_items.count() > 0:
            self.installed_items.takeItem(0)
        self.add_installed_assets()
        self.update()

    def refresh_data(self):
        reload_cached_data()
        self.create_asset_list()


class SubWindow(QWidget):
    """
    This "window" is a QWidget. If it has no parent, it
    will appear as a free-floating window as we want.
    """

    def __init__(self, widget: QWidget, title: str):
        super().__init__()
        layout = QVBoxLayout()
        self.setWindowTitle(title)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(widget)
        widget.show()
        self.setLayout(layout)


class AssetItemWidget(QtWidgets.QWidget):
    def __init__(
        self, entry: AssetEntry, asset_type: FrayToolsAssetType, parent: AssetListWidget
    ):
        super(AssetItemWidget, self).__init__(parent)
        self.main_menu = parent.parent_ref
        self.entry = entry
        self.tags = []
        self.downloading_tags: set[str] = set()
        self.selected_version = None
        self.asset_type = asset_type
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)

        if entry.asset and entry.config:
            self.tags = list(map(lambda v: v.tag, entry.asset.versions))
            log(f"{entry.display_name()}: {self.tags}")
            if (
                asset_type == FrayToolsAssetType.Plugin
                and entry.manifest
                and isinstance(entry.manifest, PluginManifest)
                and entry.manifest.version in self.tags
            ):
                self.selected_version = entry.manifest.version

        self.create_elements()
        self.update_buttons()

    def create_elements(self) -> None:
        self.row = QHBoxLayout()
        self.row.setSpacing(0)
        self.setMinimumHeight(30)

        self.refresh_action = QAction("Refresh Source", self)
        self.refresh_action.triggered.connect(
            lambda: asyncio.ensure_future(self.on_refresh())
        )
        self.delete_download_cache_action = QAction("Delete Download Cache", self)
        self.delete_download_cache_action.triggered.connect(
            self.on_remove_download_cache
        )

        self.delete_download_action = QAction("Delete Download", self)
        self.delete_download_action.triggered.connect(self.on_remove_download)

        self.remove_source_action = QAction("Remove Source", self)
        self.remove_source_action.triggered.connect(self.on_remove_source)

        self.show_changelog_action = QAction("Show Changelog", self)
        self.show_changelog_action.triggered.connect(self.on_show_changelog)

        self.download_action = QAction("Download", self)
        self.download_action.triggered.connect(
            lambda: asyncio.ensure_future(self.on_download())
        )

        self.install_action = QAction("Install", self)
        self.install_action.triggered.connect(self.on_install)

        self.uninstall_action = QAction("Uninstall", self)
        self.uninstall_action.triggered.connect(self.on_uninstall)

        self.edit_action = QAction("Edit Source")
        self.edit_action.triggered.connect(self.on_edit)

        self.addAction(self.refresh_action)
        self.addAction(self.delete_download_action)
        self.addAction(self.remove_source_action)
        self.addAction(self.edit_action)
        self.addAction(self.show_changelog_action)
        self.addAction(self.download_action)
        self.addAction(self.install_action)
        self.addAction(self.uninstall_action)

        self.text_label = QLabel("")
        self.row.addWidget(self.text_label, alignment=QtCore.Qt.AlignmentFlag.AlignLeft)

        self.uninstall_button = QPushButton("Uninstall")
        self.uninstall_button.setMaximumWidth(60)
        self.uninstall_button.pressed.connect(self.on_uninstall)
        self.row.addWidget(self.uninstall_button)

        self.install_button = QPushButton("Install")
        self.install_button.setMaximumWidth(60)
        self.install_button.pressed.connect(self.on_install)
        self.row.addWidget(self.install_button)

        self.download_button = QPushButton("Download")
        self.download_button.setMaximumWidth(90)
        self.download_button.pressed.connect(
            lambda: asyncio.ensure_future(self.on_download())
        )
        self.row.addWidget(self.download_button)

        self.installed_button = QPushButton("Installed")
        self.installed_button.setMaximumWidth(60)
        self.installed_button.setEnabled(False)
        self.row.addWidget(self.installed_button)

        self.edit_button = QPushButton("Edit")
        self.edit_button.setMaximumWidth(60)
        self.edit_button.pressed.connect(self.on_edit)
        self.row.addWidget(self.edit_button)

        self.selection_list = QComboBox(self)
        self.selection_list.currentIndexChanged.connect(self.on_select)
        self.selection_list.addItems(self.tags)
        self.selection_list.setMaximumWidth(120)
        if self.entry.manifest and self.entry.asset and self.selected_version:
            self.selection_list.setCurrentIndex(self.tags.index(self.selected_version))

        self.row.addWidget(self.selection_list)

        self.setLayout(self.row)

    @QtCore.Slot()
    def on_show_changelog(self):

        pattern = re.compile(
            r"""
        \b
        (
            (?:https?://|(?<!//)www\.)    # prefix - https:// or www.
            \w[\w_\-]*(?:\.\w[\w_\-]*)*   # host
            [^<>\s"']*                    # rest of url
            (?<![?!.,:*_~);])             # exclude trailing punctuation
            (?=[?!.,:*_~);]?(?:[<\s]|$))  # make sure that we're not followed by " or ', i.e. we're outside of href="...".
        )
    """,
            re.X,
        )
        if self.entry.asset and self.selected_version:
            log("Showing Changelog")
            md = self.entry.asset.get_changelog(self.tags.index(self.selected_version))
            html = markdown2.markdown(
                md,
                extras=["link-patterns"],
                link_patterns=[(pattern, r"\1")],
            )
            text = QTextBrowser()
            text.setHtml(html)
            text.setMinimumSize(600,600)
            text.setOpenExternalLinks(True)

            self.subwindow = SubWindow(
                text, f"Changelog for {self.entry.asset.id} {self.selected_version}"
            )
            self.subwindow.resize(text.sizeHint())
            self.subwindow.show()


    @QtCore.Slot()
    def on_edit(self) -> None:
        dialog: SourceEntryDialogue = SourceEntryDialogue(self.main_menu,self.entry.config,self.asset_type)
        dialog.exec()

    @QtCore.Slot()
    def on_remove_source(self):
        global sources_config
        try:
            if self.entry.config:
                if Cache.exists(self.entry.config.id, self.asset_type):
                    Cache.delete(self.entry.config.id, self.asset_type)
                Cache.write_to_disk()
                sources_config.remove_entry(self.entry.config.id, self.asset_type)
                sources_config.write_config()
                refresh_data_ui_offline(self)
                self.main_menu.reload()
                self.update_buttons()
        except IOError as e:
            display_error_popup(self, str(e))

    @QtCore.Slot()
    def on_remove_download_cache(self):
        try:
            if self.entry.config and self.selection_list and self.selected_version:
                download_path = download_location(self.entry.config.id, self.asset_type)
                if download_path.exists():
                    shutil.rmtree(download_path)
            refresh_data_ui_offline(self)
            self.update_buttons()
        except IOError as e:
            display_error_popup(self, str(e))

    @QtCore.Slot()
    def on_remove_download(self):
        try:
            if self.entry.config and self.selection_list and self.selected_version:
                download_path = download_location_file(
                    self.entry.config.id, self.selected_version, self.asset_type
                )
                if download_path.exists():
                    download_path.unlink(True)
            refresh_data_ui_offline(self)
            self.update_buttons()
        except IOError as e:
            display_error_popup(self, str(e))

    @QtCore.Slot()
    async def on_refresh(self):
        try:
            if self.entry.config:
                await fetch_asset_source(self.entry.config.id, self.asset_type)
            self.main_menu.reload()
            refresh_data_ui_offline(self)
            self.update_buttons()
        except (IOError, GitHubException, ValueError) as e:
            display_error_popup(self, str(e))

    @QtCore.Slot()
    def on_select(self, index: int) -> None:
        self.selected_version = self.tags[index]
        self.update_buttons()

    @QtCore.Slot()
    def on_uninstall(self) -> None:
        try:
            if self.entry.manifest:
                manifest: PluginManifest | TemplateManifest = self.entry.manifest
                msgBox: QMessageBox = QMessageBox()
                msgBox.setWindowTitle("Uninstalling plugin")
                if not self.entry.asset or len(self.tags) == 0:
                    msgBox.setText(
                        f"Are you sure you want to remove {self.entry.display_name()}?\nIt is the only version available."
                    )
                else:
                    msgBox.setText(
                        f"Are you sure you want to remove {self.entry.display_name()}?"
                    )
                msgBox.setStandardButtons(
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                msgBox.setDefaultButton(QMessageBox.StandardButton.No)
                msgBox.adjustSize()
                if msgBox.exec() == QMessageBox.StandardButton.Yes:
                    path = Path(manifest.path)
                    if path.exists():
                        shutil.rmtree(path)
                        self.entry.manifest = None
            self.main_menu.reload()
            self.update_buttons()
        except IOError as e:
            display_error_popup(self, str(e))

    @QtCore.Slot()
    def on_install(self) -> None:
        global plugin_manifest_map, template_manifest_map
        try:
            if self.entry.asset and self.selection_list:
                index: int = self.selection_list.currentIndex()
                plugin_manifests = None
                template_manifests = None
                if self.asset_type == FrayToolsAssetType.Plugin:
                    plugin_manifests = plugin_manifest_map
                elif self.asset_type == FrayToolsAssetType.Template:
                    template_manifests = template_manifest_map
                self.install_button.setText("Installing...")
                self.install_action.setText("Installing...")
                self.install_button.setEnabled(False)
                self.install_action.setEnabled(False)
                self.entry.asset.install_version(
                    index,
                    asset_type=self.asset_type,
                    plugin_manifests=plugin_manifests,
                    template_manifests=template_manifests,
                )
                reload_cached_data()
                if self.asset_type == FrayToolsAssetType.Plugin:
                    self.entry.manifest = plugin_manifest_map[self.entry.asset.id]
                elif self.asset_type == FrayToolsAssetType.Template:
                    self.entry.manifest = template_manifest_map[self.entry.asset.id]
        except IOError as e:
            display_error_popup(self, "IO ERROR HERE" + str(e))
            self.on_remove_download()
        except BaseException as e:
             display_error_popup(self, "BASE EXCEPTION HERE" + str(e))
        finally:
            self.install_button.setText("Install")
            self.install_action.setText("Install")
            self.install_button.setEnabled(True)
            self.install_action.setEnabled(True)
            self.update_buttons()

    async def on_download(self) -> None:
        global plugin_manifest_map
        if (
            self.entry.asset
            and self.selection_list
            and self.selection_list.currentData() not in self.downloading_tags
        ):
            try:
                index: int = self.selection_list.currentIndex()
                self.downloading_tags.add(self.selection_list.currentData())
                self.download_button.setEnabled(False)
                self.download_action.setEnabled(False)
                self.download_button.setText("Downloading...")
                self.download_action.setText("Downloading...")
                await self.entry.asset.download_version(index)
                reload_cached_data()
                self.update_buttons()
            finally:
                self.download_button.setEnabled(True)
                self.download_action.setEnabled(True)
                self.download_button.setText("Download")
                self.download_action.setText("Download")
                self.downloading_tags.remove(self.selection_list.currentData())
                self.update_buttons()

    def update_buttons(self) -> None:
        entry: AssetEntry = self.entry
        plugin: FrayToolsAsset | None = entry.asset

        self.text_label.setText(self.entry.display_name())

        if self.entry.config:
            self.edit_button.show()
            self.edit_action.setEnabled(True)
            self.remove_source_action.setEnabled(True)
            self.refresh_action.setEnabled(True)
        else: 
            self.edit_button.hide()
            self.remove_source_action.setEnabled(False)
            self.edit_action.setEnabled(False)
            self.refresh_action.setEnabled(False)

        if self.entry.asset:
            self.show_changelog_action.setEnabled(True)
        else:
            self.show_changelog_action.setEnabled(False)

        if self.entry.is_installed(self.selected_version):
            self.installed_button.show()
        else:
            self.installed_button.hide()
        if self.entry.can_download(self.selected_version):
            self.download_button.show()
            self.download_action.setEnabled(True)
        else:
            self.download_button.hide()
            self.download_action.setEnabled(False)

        if self.entry.can_uninstall(self.selected_version):
            log(f"Can Uninstall {self.selected_version}")
            self.uninstall_button.show()
            self.uninstall_action.setEnabled(True)
        else:
            self.uninstall_button.hide()
            self.uninstall_action.setEnabled(False)

        if self.entry.can_install(self.selected_version):
            self.install_button.show()
            self.install_action.setEnabled(True)
        else:
            self.install_button.hide()
            self.install_action.setEnabled(False)

        if plugin:
            self.selection_list.show()
        else:
            self.selection_list.hide()

        self.selection_list.update()
        self.install_button.update()
        self.installed_button.update()
        self.uninstall_button.update()
        self.text_label.update()


def main():
    app = QtWidgets.QApplication([])

    event_loop = QEventLoop(app)
    asyncio.set_event_loop(event_loop)

    app_close_event = asyncio.Event()
    app.aboutToQuit.connect(app_close_event.set)
    main_window = MainWindow()
    main_window.show()
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    with event_loop:
        event_loop.run_until_complete(app_close_event.wait())


if __name__ == "__main__":
    main()
