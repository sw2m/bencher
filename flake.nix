{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      each = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
      python = pkgs: pkgs.python3.override {
        packageOverrides = final: prev: {
          pyincusd = final.buildPythonPackage rec {
            pname = "pyincusd";
            version = "7.5.1";
            format = "wheel";
            src = final.fetchPypi {
              inherit pname version format;
              dist = "py3";
              python = "py3";
              hash = "sha256-RPIIrDl1MbaiHcc/Mp5Q6qeZThwUe8wlIv1g3z8fKbc=";
            };
            dependencies = with final; [ httpx python-dateutil pydantic typing-extensions pyyaml ];
          };
        };
      };
      bencher = pkgs: (python pkgs).pkgs.buildPythonApplication {
        pname = "bencher";
        version = "0.1.0";
        pyproject = true;
        src = self;
        build-system = [ (python pkgs).pkgs.setuptools ];
        dependencies = with (python pkgs).pkgs; [ pyincusd pydantic pydantic-settings ];
        makeWrapperArgs = [ "--prefix" "PATH" ":" (pkgs.lib.makeBinPath [ pkgs.git ]) ];
        pythonImportsCheck = [ "bencher.cli" ];
      };
      tools = pkgs: [
        ((python pkgs).withPackages (p: with p; [ pyincusd pydantic pydantic-settings mypy ]))
        pkgs.ruff
        pkgs.git
      ];
    in
    {
      packages = each (pkgs: { default = bencher pkgs; });
      apps = each (pkgs: {
        default = { type = "app"; program = "${bencher pkgs}/bin/bencher"; };
      });
      devShells = each (pkgs: { default = pkgs.mkShell { packages = tools pkgs; }; });
      checks = each (pkgs: {
        package = bencher pkgs;
        lint = pkgs.runCommand "lint" { nativeBuildInputs = tools pkgs; } ''
          cd ${self}
          export RUFF_CACHE_DIR=$TMPDIR/ruff
          ruff check src
          ruff format --check src
          touch $out
        '';
        types = pkgs.runCommand "types" { nativeBuildInputs = tools pkgs; } ''
          cd ${self}
          mypy --cache-dir=$TMPDIR/mypy -p bencher
          touch $out
        '';
      });
    };
}
