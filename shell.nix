{ pkgs ? import <nixpkgs> {} }:
let
  python-packages = ps: with ps; [
    pyside6
    pygithub
    aiohttp
    qasync
    githubkit
    # other python packages
  ];
in pkgs.mkShell {
    packages = [ (pkgs.python3.withPackages python-packages)];
  }
