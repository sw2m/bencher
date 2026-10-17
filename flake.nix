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
      runtime = p: with p; [ deepmerge httpx pydantic pydantic-settings pyincusd pyyaml scipy ];
      bencher = pkgs: (python pkgs).pkgs.buildPythonApplication {
        pname = "bencher";
        version = "0.1.0";
        pyproject = true;
        src = self;
        build-system = [ (python pkgs).pkgs.setuptools ];
        dependencies = runtime (python pkgs).pkgs;
        pythonImportsCheck = [ "bencher.cli.bencher" ];
      };
      tools = pkgs: [
        ((python pkgs).withPackages (p: runtime p ++ [ p.mypy p.types-pyyaml ]))
        pkgs.ruff
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
        cloud = pkgs.runCommand "cloud" { nativeBuildInputs = tools pkgs ++ [ pkgs.cloud-init ]; } ''
          PYTHONPATH=${self}/src python - "$TMPDIR/rendered" <<'EOF'
          import sys
          from pathlib import Path

          from bencher.host.target import Target

          out = Path(sys.argv[1])
          out.mkdir()
          for target in Target.every():
              (out / f"{target.distro}.{target.release}.{target.kind}.yaml").write_text(target.compose())
          EOF
          for config in $TMPDIR/rendered/*.yaml; do
            cloud-init schema --config-file "$config"
          done
          touch $out
        '';
      });
    };
}
