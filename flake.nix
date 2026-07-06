{
  description = "UE5 autoresearch dev environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils, ... }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
      in
      {
        devShells.default = pkgs.mkShell {
          name = "ue5-autoresearch";

          buildInputs = with pkgs; [
            # Language/version tooling
            mise
            uv

            # Local LLM server (OpenAI-compatible)
            llama-cpp

            # Version control
            git
            jujutsu

            # TLS certificates for uv downloads
            cacert
          ];

          shellHook = ''
            # Point Xcode tooling at the real Xcode install instead of the
            # Nix apple-sdk stub, so xcodebuild/xcrun/shader compilation work.
            export DEVELOPER_DIR="/Applications/Xcode.app/Contents/Developer"

            echo "UE5 autoresearch dev shell"
            echo "  mise install      # install pinned python/uv"
            echo "  uv sync           # install python deps"
            echo "  llama-server ...  # start the local LLM server"
            echo "  mise run propose  # start the M2 loop"
          '';
        };
      }
    );
}
