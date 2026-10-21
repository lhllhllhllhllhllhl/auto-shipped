from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "guanyi-server"


def test_server_deployment_has_no_embedded_target_or_credentials() -> None:
    checked = [
        DEPLOY / "deploy.sh",
        DEPLOY / "preflight.sh",
        DEPLOY / "provision.sh",
        DEPLOY / "install-release.sh",
        DEPLOY / "doctor.sh",
        DEPLOY / "guanyi-server.env.example",
    ]
    content = "\n".join(path.read_text(encoding="utf-8") for path in checked)

    assert "111.229.207.29" not in content
    assert "sy123456" not in content
    assert "GUANYI_PASSWORD=" not in content
    assert "GUANYI_USERNAME=" not in content


def test_server_environment_keeps_one_browser_state_machine() -> None:
    environment = (DEPLOY / "guanyi-server.env.example").read_text(encoding="utf-8")

    assert "GUANYI_BROWSER_BACKEND=persistent" in environment
    assert "GUANYI_PROFILE_DIR=/var/lib/auto-shipped/guanyi-browser/profile" in environment
    assert "GUANYI_OPERATION_DIR=/var/lib/auto-shipped/guanyi-browser/operations" in environment
    assert "GUANYI_LOGIN_OPERATION_DIR=/var/lib/auto-shipped/guanyi-browser/login-operations" in environment


def test_server_deployment_documents_all_recovery_commands() -> None:
    readme = (DEPLOY / "README.md").read_text(encoding="utf-8")

    for command in ["preflight.sh", "provision.sh", "install-release.sh", "doctor.sh", "deploy.sh"]:
        assert f"`{command}`" in readme


def test_ubuntu_release_uses_supported_python_and_readable_code() -> None:
    provision = (DEPLOY / "provision.sh").read_text(encoding="utf-8")
    installer = (DEPLOY / "install-release.sh").read_text(encoding="utf-8")

    assert "python3.11-venv" in provision
    assert "python3.11" in installer
    assert 'chmod -R a+rX "$release_dir"' in installer
