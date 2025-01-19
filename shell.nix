{pkgs ? import <nixpkgs> {}}: let
  python-packages = ps:
    with ps; [
      pyside6
      aiohttp
      qasync
      requirements-parser
      markdown2
      mypy
      zipfile2
      cx-freeze
      aiogithubapi
    ];
in
  pkgs.mkShell {
    packages = [pkgs.appstream pkgs.pipenv (pkgs.python3.withPackages python-packages)];
    buildInputs = with pkgs; [libsForQt5.qt5.qtbase libz];
  }
