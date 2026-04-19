{ pkgs, ... }: {
  # Channel to use
  channel = "stable-24.11";

  # Packages to install in the environment
  packages = [
    pkgs.python3
    pkgs.docker-compose
  ];

  # Enable Docker service
  services.docker.enable = true;

  # Set environment variables
  env = {};

  # IDX specific configurations
  idx = {
    # Search for extensions at https://open-vsx.org/
    extensions = [
      "ms-azuretools.vscode-docker"
      "ms-python.python"
    ];

    # Workspace lifecycle hooks
    workspace = {
      # Runs when a workspace is first created
      onCreate = {
        # Example: install dependencies
        # pip-install = "pip install -r requirements.txt";
      };
      # Runs when a workspace is (re)started
      onStart = {
        # Example: start a background task
      };
    };

    # Previews configuration
    previews = {
      enable = true;
      previews = {
        # web = {
        #   command = ["python" "-m" "http.server" "$PORT"];
        #   manager = "web";
        # };
      };
    };
  };
}
