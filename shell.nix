{pkgs ? import <nixpkgs> {}}: let
  python-packages = ps:
    with ps; [
      (
        pyside6.overrideAttrs (
          final: prev: {
            buildInputs = with pkgs.python3.pkgs.qt6; [
              pkgs.python3.pkgs.ninja
              pkgs.python3.pkgs.packaging
              pkgs.python3.pkgs.setuptools
              qtbase
            ];
          }
        )
      )
      cx-freeze
      aiohttp
      qasync
      githubkit
      requirements-parser
      markdown2
      nuitka
      mypy
    ];
in
  pkgs.mkShell {
    packages = [pkgs.pipenv (pkgs.python3.withPackages python-packages)];
  }
