import os
from sys import version
from typing import Self, Type, Generator
from os import path
import json
from pathlib import Path
from github import Github
from urllib.request import urlretrieve
import zipfile
import pprint
import sys
import random
from PySide6 import QtCore, QtWidgets, QtGui

from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMenuBar,
    QPushButton,
    QSpacerItem,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)
import github
gh = Github()


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
    return Path.home().joinpath("FrayToolsData", "plugins")


def template_directory() -> Path:
    return Path.home().joinpath("FrayToolsData", "templates")


class PluginConfig:
    def __init__(self, owner: str, repo: str, id: str):
        self.owner = owner
        self.repo = repo
        self.id = id


class TemplateConfig:
    def __init__(self, owner: str, repo: str):
        self.owner = owner
        self.repo = repo


class SourcesConfig:
    def __init__(self, plugins: list[PluginConfig], templates: list[TemplateConfig]):
        self.plugins = plugins
        self.templates = templates

    @staticmethod
    def from_config(path: str):
        plugins: list[PluginConfig] = []
        templates: list[TemplateConfig] = []
        with open(path) as config_data:
            config = json.load(config_data)
            for entry in config["plugins"]:
                plugin_config = PluginConfig(entry["owner"], entry["repo"], entry["id"])
                plugins.append(plugin_config)
            for entry in config["templates"]:
                template_config = TemplateConfig(entry["owner"], entry["repo"])
                templates.append(template_config)
            return SourcesConfig(plugins, templates)

    def generate_plugin_config_map(self) -> dict[str, PluginConfig]:
        plugin_map: dict[str, PluginConfig] = dict()
        for plugin in self.plugins:
            plugin_map[plugin.id] = plugin
        return plugin_map


class PluginManifest:
    def __init__(
        self,
        name: str,
        plugin_type: str,
        id: str,
        version: str,
        description: str,
        path: str,
    ):
        self.plugin_type = plugin_type
        self.id = id
        self.version = version
        self.description = description
        self.path = path
        self.name = name


class FrayToolsPluginVersion:
    def __init__(self, url: str, tag: str):
        self.url = url
        self.tag = tag


class FrayToolsPlugin:
    def __init__(
        self, id: str, owner: str, repo: str, versions: list[FrayToolsPluginVersion]
    ):
        self.id = id
        self.repo = repo
        self.owner = owner
        self.versions = versions

    @staticmethod
    def fetch_data(config: PluginConfig):
        global gh
        id = config.id
        owner = config.owner
        repo_name = config.repo
        repo = gh.get_repo(f"{owner}/{repo_name}")
        releases = repo.get_releases()
        versions: list[FrayToolsPluginVersion] = []
        for release in releases:
            asset_url = release.assets[0].browser_download_url
            tag = release.tag_name
            plugin_version = FrayToolsPluginVersion(asset_url, tag)
            versions.append(plugin_version)

        return FrayToolsPlugin(id, owner, repo_name, versions)

    def download_version(
        self, index: int, manifests: dict[str, PluginManifest] | None = None
    ):
        download_url = self.versions[index].url
        tag = self.versions[index].tag
        name = self.id
        filename = f"{name}-{tag}.zip"
        _, _ = urlretrieve(download_url, filename)
        manifest_path = None

        if manifests is not None and self.id in manifests.keys():
            manifest_path = manifests[self.id].path
        if manifest_path is not None:
            extract_zip_without_root(filename, str(manifest_path))
        else:
            outpath = plugin_directory().joinpath(name)
            if not os.path.isdir(outpath):
                os.makedirs(outpath)
            extract_zip_without_root(filename, str(outpath))


class PluginEntry:
    def __init__(self, manifest: PluginManifest | None, config: PluginConfig | None, plugin: FrayToolsPlugin | None):
        self.plugin = plugin
        self.manifest = manifest
        self.config = config


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


def generate_manifest_map(manifests: list[PluginManifest]) -> dict[str, PluginManifest]:
    manifest_map: dict[str, PluginManifest] = dict()
    for manifest in manifests:
        manifest_map[manifest.id] = manifest
    return manifest_map


def generate_plugin_map(plugins: list[FrayToolsPlugin]) -> dict[str, FrayToolsPlugin]:
    plugin_map: dict[str, FrayToolsPlugin] = dict()
    for plugin in plugins:
        plugin_map[plugin.id] = plugin
    return plugin_map


def generate_config_map(plugins: list[PluginConfig]) -> dict[str, PluginConfig]:
    config_map: dict[str, PluginConfig] = dict()
    for plugin in plugins:
        config_map[plugin.id] = plugin
    return config_map


sources_config: SourcesConfig
config_map: dict[str, PluginConfig] = dict()
manifest_map: dict[str, PluginManifest] = dict()
plugin_entries: list[PluginEntry] = []
plugin_map: dict[str, FrayToolsPlugin] = dict()


def generate_plugin_entries() -> list[PluginEntry]:
    global plugin_entries, config_map, manifest_map
    installed_entries: list[PluginEntry] = list(
        map(lambda m: PluginEntry(m, None, None), manifest_map.values())
    )

    uninstalled_entries: list[PluginEntry] = list(
        map(
            lambda c: PluginEntry(None, c, None),
            (filter(lambda p: p.id not in manifest_map.keys(), config_map.values())),
        )
    )

    for entry in installed_entries:
        if entry.manifest and entry.manifest.id in config_map.keys():
            entry.config = config_map[entry.manifest.id]
        if entry.manifest and entry.manifest.id in plugin_map.keys():
            entry.plugin = plugin_map[entry.manifest.id]
            

    
    
    entries:list[PluginEntry] = installed_entries + uninstalled_entries
    plugin_entries = entries
    
    return entries



class PluginListWidget(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()

        # self.hello = ["Hallo Welt", "Hei maailma", "Hola Mundo", "Привет мир"]
        self.button = QtWidgets.QPushButton("Click me!")
        self.text = QtWidgets.QLabel(
            "Fraytools Manager", alignment=QtCore.Qt.AlignCenter
        )
        self.refresh_data()
        self.create_plugin_list()
        self.add_installed_plugins()

        menubar = QMenuBar(self)
        menubar.show()

        self.layout = QtWidgets.QVBoxLayout(self)
        self.layout.addWidget(self.text)
        self.layout.addWidget(self.installed_items)

        self.button.clicked.connect(self.magic)

    def create_plugin_list(self):
        self.installed_items = QListWidget()

    def add_installed_plugins(self):
        self.installed_items.clear()
        for entry in plugin_entries:
            item = QListWidgetItem(self.installed_items)
            row = PluginItemWidget(entry, self)
            self.installed_items.addItem(item)
            self.installed_items.setItemWidget(item, row)
            item.setSizeHint(row.minimumSizeHint())

    def refresh_data(self):
        global manifest_map, plugin_entries, plugin_map, config_map
        plugins:list[FrayToolsPlugin] = []
        for config in config_map.values():
            plugin:FrayToolsPlugin = FrayToolsPlugin.fetch_data(config)
            plugins.append(plugin)

        manifest_map = generate_manifest_map(detect_plugins())
        plugin_map = generate_plugin_map(plugins)
        plugin_entries = generate_plugin_entries()
        self.create_plugin_list()

    @QtCore.Slot()
    def magic(self):
        self.text.setText(random.choice(self.hello))


class PluginItemWidget(QtWidgets.QWidget):
    def __init__(self, entry: PluginEntry, parent=None):
        super(PluginItemWidget, self).__init__(parent)

        self.entry = entry
        self.render_entry()

    def render_entry(self):
        self.row = QHBoxLayout()
        self.row.setSpacing(0)
        self.setMinimumHeight(30)
        
        entry:PluginEntry = self.entry
        display_name:str = ""
        if entry.manifest:
            display_name = f'{entry.manifest.name} ({entry.manifest.id})'
        elif entry.plugin:
            display_name = f'{entry.plugin.id}'
        elif entry.config:
            display_name = f'{entry.config.id}'

        text_label = QLabel(display_name)
        self.row.addWidget(text_label, alignment=QtCore.Qt.AlignmentFlag.AlignLeft)

        if entry.manifest:
            uninstall_button = QPushButton("Uninstall")
            uninstall_button.setMaximumWidth(60)
            self.row.addWidget(uninstall_button)

        if entry.plugin:
            installed_button = QPushButton("Installed")
            installed_button.setMaximumWidth(60)
            installed_button.setEnabled(False)
            self.row.addWidget(installed_button)

            
            selection_list = QComboBox(self)
            selection_list.setPlaceholderText("Select Version")
            version_strings:list[str] = list(map(lambda v: v.tag ,entry.plugin.versions))
            selection_list.addItems(version_strings)
            selection_list.setMaximumWidth(120)
            self.row.addWidget(selection_list)
            if entry.manifest:
                selection_list.currentData(version_strings.index(entry.manifest.version))

        self.setLayout(self.row)
        

if __name__ == "__main__":
    app = QtWidgets.QApplication([])
    sources_config = SourcesConfig.from_config("./sources.json")
    config_map = sources_config.generate_plugin_config_map()
    manifest_map = generate_manifest_map(detect_plugins())
    plugin_entries = generate_plugin_entries()
    widget = PluginListWidget()
    widget.resize(800, 600)
    widget.show()

    sys.exit(app.exec())


def main():
    p = map(
        lambda x: [x.id, x.description, x.version, x.path, x.name], detect_plugins()
    )


# main()
