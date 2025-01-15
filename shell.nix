{ pkgs ? import <nixpkgs> {} }:
let
  python-packages = ps: with ps; [
    pyside6
    aiohttp
    qasync
    githubkit
    mypy
  ];
in pkgs.mkShell {
    packages = [ (pkgs.python3.withPackages python-packages)];
  }
