import os
import pprint
from re import template
from typing import Generator, TypedDict, assert_type
import json
from pathlib import Path
from attr import dataclass
from github import Github
import zipfile

import platform
from PySide6 import QtCore, QtWidgets
import shutil
from qasync import QEventLoop
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QWidget,
)
import asyncio
import aiohttp
from enum import Enum
from dataclasses import dataclass

gh = Github()


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
        if root:
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


def download_location(id: str, tag: str, asset_type: FrayToolsAssetType) -> Path:
    match asset_type:
        case FrayToolsAssetType.Plugin:
            return cache_directory().joinpath("plugins", f"{id}")
        case FrayToolsAssetType.Template:
            return cache_directory().joinpath("templates", f"{id}")


def download_location_file(id: str, tag: str, asset_type: FrayToolsAssetType) -> Path:
    return download_location(id, tag, asset_type).joinpath(f"{id}-{tag}.zip")


class AssetConfig:
    def __init__(self, owner: str, repo: str, id: str):
        self.owner = owner
        self.repo = repo
        self.id = id


class SourcesConfig:
    def __init__(self, plugins: list[AssetConfig], templates: list[AssetConfig]):
        self.plugins = plugins
        self.templates = templates

    @staticmethod
    def from_config(path: str):
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
            return SourcesConfig(plugins, templates)

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

    def write_config(self):
        import pprint

        p = self.generate_map()
        pprint.pp(p)
        config_text: str = json.dumps(self.generate_map(), indent=2)
        with open(str(app_directory().joinpath("sources.json")), "w") as f:
            f.write(config_text)

    @staticmethod
    def generate_default_config():
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
                    owner="Fraymakers", repo="stage-template", id="musictemplate"
                ),
            ],
        )


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
    def fetch_data(config: AssetConfig | AssetConfig, asset_type: FrayToolsAssetType):
        global gh
        id = config.id
        owner = config.owner
        repo_name = config.repo
        repo = gh.get_repo(f"{owner}/{repo_name}")
        releases = repo.get_releases()
        versions: list[FrayToolsAssetVersion] = []
        for release in releases:
            asset_url = release.assets[0].browser_download_url
            tag = release.tag_name
            plugin_version = FrayToolsAssetVersion(asset_url, tag)
            versions.append(plugin_version)

        return FrayToolsAsset(asset_type, id, owner, repo_name, versions)

    async def download_version(self, index: int):
        download_url = self.versions[index].url
        tag: str = self.versions[index].tag
        name: str = self.id
        print(f"Starting Download of {name}-{tag}")
        download_path: Path = download_location(name, tag, self.asset_type)
        if not download_path.exists():
            os.makedirs(download_path)

        filename: str = str(download_location_file(name, tag, self.asset_type))
        async with aiohttp.ClientSession() as session:
            async with session.get(download_url) as response:
                with open(filename, mode="wb") as file:
                    while True:
                        chunk = await response.content.read()
                        if not chunk:
                            print(f"Finished Downloading {name}-{tag}")
                            break
                        file.write(chunk)

    def install_version(
        self,
        index: int,
        asset_type: FrayToolsAssetType,
        plugin_manifests: dict[str, PluginManifest] | None = None,
        template_manifests: dict[str, TemplateManifest] | None = None,
    ):
        tag: str = self.versions[index].tag
        name: str = self.id
        manifests: dict[str, PluginManifest] | dict[str, TemplateManifest] = dict()
        outputdir = None
        if asset_type == FrayToolsAssetType.Plugin:
            outputdir = plugin_directory()
            manifests = plugin_manifests

        elif asset_type == FrayToolsAssetType.Template:
            outputdir = template_directory()
            manifests = template_manifests

        manifest_path = None
        download_path: Path = download_location(name, tag, asset_type)
        if not download_path.exists():
            os.makedirs(download_path)

        filename: str = str(download_location_file(name, tag, asset_type))
        if manifests is not None and self.id in manifests.keys():
            manifest_path = manifests[self.id].path

        if manifest_path is not None:
            extract_zip_without_root(filename, str(manifest_path))
        else:
            outpath = outputdir.joinpath(name)
            if not os.path.isdir(outpath):
                os.makedirs(outpath)
            extract_zip_without_root(filename, str(outpath))
        pass


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
        match asset_type:
            case FrayToolsAssetType.Plugin:
                self.manifest = plugin_manifest
            case FrayToolsAssetType.Template:
                self.manifest = plugin_manifest

    def display_name(self) -> str:
        display_name: str = ""
        if self.manifest and self.asset_type == FrayToolsAssetType.Plugin:
            display_name = f"{self.manifest.name} ({self.manifest.id})"
        elif self.asset:
            display_name = f"{self.asset.id}"
        elif self.config:
            display_name = f"{self.config.id}"
        return display_name

    def is_installed(self, selected_version: str | None) -> bool:
        return (self.manifest is not None and self.asset is None) or (
            self.asset is not None
            and self.manifest is not None
            and self.manifest.version == selected_version
        )

    def can_download(self, selected_version: str | None) -> bool:
        return (
            not self.is_installed(selected_version)
            and self.asset is not None
            and selected_version is not None
            and not download_location_file(
                self.asset.id, selected_version, FrayToolsAssetType.Plugin
            ).exists()
        )

    def can_uninstall(self, selected_version: str | None) -> bool:
        return self.is_installed(selected_version)

    def can_install(self, selected_version: str | None) -> bool:
        return (not self.can_download(selected_version)) and (
            not self.is_installed(selected_version)
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
                    pprint.pp(manifest)
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


class CachedFrayToolsAsset(TypedDict):
    id: str
    owner: str
    repo: str
    # asset_type: FrayToolsAssetType
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
                    lambda x: CachedFrayToolsAssetVersion(url=x.url, tag=x.tag),
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
                    lambda x: FrayToolsAssetVersion(url=x["url"], tag=x["tag"]),
                    asset["versions"],
                )
            ),
        )

    @staticmethod
    def clear():
        global sources_cache
        print("Clearing Cache..")
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
        json_str: str = json.dumps(sources_cache)
        print("Writing to cache on disk...")
        with open(cache_directory().joinpath("sources-lock.json"), "w") as f:
            f.write(json_str)
            print("Successfully wrote to cache on disk")

    @staticmethod
    def read_from_disk():
        global sources_cache
        cache_file = cache_directory().joinpath("sources-lock.json")
        if cache_file.exists() and cache_file.is_file:
            with open(cache_file, "r") as f:
                print("Reading cache from disk...")
                sources_cache = json.loads(f.read())
                print("Successfully read cache from disk.")


def generate_template_entries() -> list[AssetEntry]:
    return generate_entries(FrayToolsAssetType.Template)


def generate_plugin_entries() -> list[AssetEntry]:
    return generate_entries(FrayToolsAssetType.Plugin)


def generate_entries(asset_type: FrayToolsAssetType) -> list[AssetEntry]:
    global plugin_entries, plugin_config_map, plugin_manifest_map, plugin_map
    global template_entries, template_config_map, template_manifest_map, template_map
    config_map = dict()
    manifest_map = dict()
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
            
    installed_entries: list[AssetEntry] = list(
        map(
            lambda manifest: AssetEntry(
                plugin_manifest=manifest,
                template_manifest=None,
                config=None,
                asset=None,
                asset_type=asset_type,
            ),
            manifest_map.values(),
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


def refresh_data(fetch=False, asset_type: FrayToolsAssetType | None = None):
    global sources_cache, sources_config
    global plugin_manifest_map, plugin_entries, plugin_map, plugin_config_map
    global template_manifest_map, template_entries, template_map, template_config_map

    if app_directory().joinpath("sources.json").exists():
        sources_config = SourcesConfig.from_config(
            str(app_directory().joinpath("sources.json"))
        )
    else:
        sources_config = SourcesConfig.generate_default_config()
        sources_config.write_config()

    Cache.read_from_disk()
    plugin_config_map = generate_config_map(sources_config.plugins)
    if asset_type is None or asset_type == FrayToolsAssetType.Plugin:
        print("Refreshing Plugin Sources...")
        plugin_assets: list[FrayToolsAsset] = []
        for config in plugin_config_map.values():
            template: FrayToolsAsset
            if Cache.exists(config.id, FrayToolsAssetType.Plugin):
                template = Cache.get(config.id, FrayToolsAssetType.Plugin)
                print(f"Found {config.id} in cache")
            elif fetch:
                print(f"Could not find {config.id} in cache")
                print(f"Fetching {config.id}")
                template = FrayToolsAsset.fetch_data(config, FrayToolsAssetType.Plugin)
                Cache.add(template, FrayToolsAssetType.Plugin)
                print(f"Added {config.id} to cache")
            else:
                continue
            plugin_assets.append(template)
        plugin_manifest_map = generate_manifest_map(detect_plugins())
        plugin_map = generate_asset_map(plugin_assets)

    template_config_map = generate_config_map(sources_config.templates)
    if asset_type is None or asset_type == FrayToolsAssetType.Template:
        print("Refreshing Plugin Sources...")
        template_assets: list[FrayToolsAsset] = []
        for config in template_config_map.values():
            template: FrayToolsAsset
            if Cache.exists(config.id, FrayToolsAssetType.Template):
                template = Cache.get(config.id, FrayToolsAssetType.Template)
                print(f"Found {config.id} in cache")
            elif fetch:
                print(f"Could not find {config.id} in cache")
                print(f"Fetching {config.id}")
                template = FrayToolsAsset.fetch_data(
                    config, FrayToolsAssetType.Template
                )
                Cache.add(template, FrayToolsAssetType.Plugin)
                print(f"Added {config.id} to cache")
            else:
                continue
            template_assets.append(template)
        template_manifest_map = generate_manifest_map(detect_templates())
        template_map = generate_asset_map(template_assets)

    plugin_entries = generate_plugin_entries()
    template_entries = generate_template_entries()
    print("template_entries")
    print(template_entries)
    print("plugin_entries")
    print(plugin_entries)
    
    Cache.write_to_disk()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.setWindowTitle("FrayTools Manager")
        self.tabs = QTabWidget(self)
        self.plugin_list = AssetListWidget(FrayToolsAssetType.Plugin)
        self.template_list = AssetListWidget(FrayToolsAssetType.Template)
        self.settings_menu = SettingsWidget(self)
        self.tabs.addTab(self.plugin_list, "Plugins")
        self.tabs.addTab(self.template_list, "Templates")
        self.tabs.addTab(QWidget(), "Sources")
        self.tabs.addTab(self.settings_menu, "Settings")
        self.setCentralWidget(self.tabs)
        self.setMinimumSize(QtCore.QSize(800, 600))

        self.show()


class SettingsWidget(QtWidgets.QWidget):
    def __init__(self, parent: MainWindow) -> None:
        super().__init__()
        self.parent_ref = parent
        self.layout = QtWidgets.QVBoxLayout(self)
        self.settings_items = QListWidget()
        self.settings_items.setUniformItemSizes(True)
        self.settings_items.setSpacing(10)
        self.layout.addWidget(self.settings_items)
        self.simple_settings_button(
            "Clear Sources Cache", "Clears the sources cache", self.clear_sources_cache
        )
        self.simple_settings_button(
            "Clear Download Cache",
            "Clears the sources cache",
            self.clear_download_cache,
        )
        self.simple_settings_button(
            "Refresh",
            "Updates Plugin Metadata, Subject to Github API Limits",
            self.refresh_sources,
        )

    def simple_settings_button(self, button_text: str, description: str, on_press):
        widget = QWidget()
        widget.setMinimumHeight(40)
        row = QHBoxLayout()
        text = QLabel(description)
        button = QPushButton(button_text)
        button.pressed.connect(on_press)
        row.addWidget(text)
        row.addWidget(button)
        widget.setLayout(row)
        item = QListWidgetItem(self.settings_items)
        item.setSizeHint(row.sizeHint())
        self.settings_items.addItem(item)
        self.settings_items.setItemWidget(item, widget)

    def refresh_parent(self):
        self.parent_ref.plugin_list.reload()

    @QtCore.Slot()
    def clear_sources_cache(self):
        msgBox: QMessageBox = QMessageBox()
        msgBox.setWindowTitle("Clear Sources Cache")
        msgBox.setText("Are you sure you want to clear the sources Cache?")
        msgBox.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        msgBox.setDefaultButton(QMessageBox.StandardButton.No)
        if msgBox.exec() == QMessageBox.StandardButton.Yes:
            Cache.clear()
            Cache.write_to_disk()
            refresh_data()
            self.refresh_parent()
        else:
            pass

    @QtCore.Slot()
    def clear_download_cache(self):
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
            refresh_data()
            self.refresh_parent()
        else:
            pass

    @QtCore.Slot()
    def refresh_sources(self):
        msgBox: QMessageBox = QMessageBox()
        msgBox.setWindowTitle("Refresh Sources")
        msgBox.setText(
            "Refreshing sources too often might result in hitting API limits, are you sure you want to proceed?"
        )
        msgBox.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        msgBox.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if msgBox.exec() == QMessageBox.StandardButton.Yes:
            Cache.clear()
            Cache.write_to_disk()
            refresh_data(True)
            self.refresh_parent()
        else:
            pass


class AssetListWidget(QtWidgets.QWidget):
    def __init__(self, asset_type: FrayToolsAssetType):
        super().__init__()

        self.asset_type = asset_type
        self.refresh_data()
        self.create_asset_list()
        self.add_installed_assets()

        self.layout = QtWidgets.QVBoxLayout(self)
        self.layout.addWidget(self.installed_items)

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
                pprint.pp(list(map(lambda x: pprint.pp(x), template_entries)))
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
        refresh_data()
        self.create_asset_list()


class AssetItemWidget(QtWidgets.QWidget):
    def __init__(self, entry: AssetEntry, asset_type: FrayToolsAssetType, parent=None):
        super(AssetItemWidget, self).__init__(parent)

        self.entry = entry
        self.tags = []
        self.downloading_tags = set()
        self.selected_version = None
        self.asset_type = asset_type

        if entry.asset or (
            entry.asset
            and entry.config
            and self.asset_type == FrayToolsAssetType.Template
        ):
            self.tags = list(map(lambda v: v.tag, entry.asset.versions))
            if entry.manifest and entry.manifest.version in self.tags:
                self.selected_version = entry.manifest.version

        self.create_elements()
        self.update_buttons()

    def create_elements(self) -> None:
        self.row = QHBoxLayout()
        self.row.setSpacing(0)
        self.setMinimumHeight(30)

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

        self.selection_list = QComboBox(self)
        self.selection_list.currentIndexChanged.connect(self.on_select)
        self.selection_list.addItems(self.tags)
        self.selection_list.setMaximumWidth(120)
        if self.entry.manifest and self.entry.asset and self.selected_version:
            self.selection_list.setCurrentIndex(self.tags.index(self.selected_version))

        self.row.addWidget(self.selection_list)

        self.setLayout(self.row)

    @QtCore.Slot()
    def on_select(self, index: int) -> None:
        self.selected_version = self.tags[index]
        self.update_buttons()

    @QtCore.Slot()
    def on_uninstall(self) -> None:
        msgBox: QMessageBox = QMessageBox()
        if self.entry.manifest:
            manifest: PluginManifest = self.entry.manifest
            msgBox.setWindowTitle(f"Uninstalling plugin")
            if not self.entry.asset or len(self.tags) == 0:
                msgBox.setText(
                    f"Are you sure you want to remove {manifest.name} ({manifest.version})?\nIt is the only version available."
                )
            else:
                msgBox.setText(
                    f"Are you sure you want to remove {manifest.name} ({manifest.version})?"
                )
            msgBox.setStandardButtons(
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            msgBox.setDefaultButton(QMessageBox.StandardButton.No)
            if msgBox.exec() == QMessageBox.StandardButton.Yes:
                path = Path(manifest.path)
                if path.exists():
                    shutil.rmtree(path)
                    self.entry.manifest = None
        refresh_data()
        self.update_buttons()

    @QtCore.Slot()
    def on_install(self) -> None:
        global plugin_manifest_map, template_manifest_map
        if self.entry.asset and self.selection_list:
            index: int = self.selection_list.currentIndex()
            plugin_manifests = None
            template_manifests = None
            if self.asset_type == FrayToolsAssetType.Plugin:
                plugin_manifests = plugin_manifest_map
            elif self.asset_type == FrayToolsAssetType.Template:
                template_manifests = template_manifest_map
            self.entry.asset.install_version(
                index,
                asset_type=self.asset_type,
                plugin_manifests=plugin_manifests,
                template_manifests=template_manifests,
            )
            refresh_data()
            if self.asset_type == FrayToolsAssetType.Plugin:
                self.entry.manifest = plugin_manifest_map[self.entry.asset.id]
            elif self.asset_type == FrayToolsAssetType.Template:
                self.entry.manifest = plugin_manifest_map[self.entry.asset.id]
            self.update_buttons()

    async def on_download(self) -> None:
        global plugin_manifest_map
        if (
            self.entry.asset
            and self.selection_list
            and self.selection_list.currentData() not in self.downloading_tags
        ):
            index: int = self.selection_list.currentIndex()
            self.downloading_tags.add(self.selection_list.currentData())
            self.download_button.setEnabled(False)
            self.download_button.setText("Downloading...")
            await self.entry.asset.download_version(index)
            self.download_button.setEnabled(True)
            self.download_button.setText("Download")
            self.downloading_tags.remove(self.selection_list.currentData())
            refresh_data()
            self.update_buttons()

    def update_buttons(self) -> None:
        entry: AssetEntry = self.entry
        plugin: FrayToolsAsset | None = entry.asset

        self.text_label.setText(self.entry.display_name())

        if self.entry.is_installed(self.selected_version):
            self.installed_button.show()
        else:
            self.installed_button.hide()
        if self.entry.can_download(self.selected_version):
            self.download_button.show()
        else:
            self.download_button.hide()

        if self.entry.can_uninstall(self.selected_version):
            print(f"Can Uninstall {self.selected_version}")
            self.uninstall_button.show()
        else:
            self.uninstall_button.hide()

        if self.entry.can_install(self.selected_version):
            self.install_button.show()
        else:
            self.install_button.hide()

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

    refresh_data()

    event_loop = QEventLoop(app)
    asyncio.set_event_loop(event_loop)

    app_close_event = asyncio.Event()
    app.aboutToQuit.connect(app_close_event.set)

    main_window = MainWindow()
    main_window.show()

    with event_loop:
        event_loop.run_until_complete(app_close_event.wait())


if __name__ == "__main__":
    main()
