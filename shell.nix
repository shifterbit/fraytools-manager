{pkgs ? import <nixpkgs> {}}: let
  python-packages = ps:
    with ps; [
      pyside6
      cx-freeze
      aiohttp
      qasync
      githubkit
      requirements-parser
      markdown2
      nuitka
      mypy
      pyinstaller
      pyinstaller-hooks-contrib
      zipfile2
    ];
in
  pkgs.mkShell {
    packages = [pkgs.pipenv (pkgs.python3.withPackages python-packages)];
    buildInputs = with pkgs; [libz];
  }
