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
        DEPLOY / "remote-job.sh",
        DEPLOY / "guanyi-server.env.example",
    ]
    content = "\n".join(path.read_text(encoding="utf-8") for path in checked)

    assert "AUTO_SHIPPED_TARGET_HOST=" not in content
    assert "GUANYI_LOGIN_PASSWORD=" not in content
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

    for command in ["preflight.sh", "provision.sh", "install-release.sh", "doctor.sh", "deploy.sh", "remote-job.sh"]:
        assert f"`{command}`" in readme


def test_remote_job_wrapper_exposes_only_registered_executor_actions() -> None:
    wrapper = (DEPLOY / "remote-job.sh").read_text(encoding="utf-8")

    for action in [
        "health",
        "init",
        "put-manifest",
        "put-artifact",
        "verify",
        "status",
        "prepare",
        "execute",
        "reconcile",
        "probe",
        "login-start",
        "login-status",
        "login-credentials",
        "login-sms-request",
        "login-sms-submit",
    ]:
        assert action in wrapper
    for forbidden in ["submit-import", "select-import-file", "order-audit"]:
        assert forbidden not in wrapper

    assert "GUANYI_BROWSER_BACKEND" in wrapper
    assert "GUANYI_LOGIN_OPERATION_DIR" in wrapper


def test_ubuntu_release_uses_supported_python_and_readable_code() -> None:
    provision = (DEPLOY / "provision.sh").read_text(encoding="utf-8")
    installer = (DEPLOY / "install-release.sh").read_text(encoding="utf-8")

    assert "python3.11-venv" in provision
    assert "python3.11" in installer
    assert 'chmod -R a+rX "$release_dir"' in installer


def test_node_registry_is_configurable_without_changing_the_lockfile() -> None:
    environment = (DEPLOY / "guanyi-server.env.example").read_text(encoding="utf-8")
    installer = (DEPLOY / "install-release.sh").read_text(encoding="utf-8")

    assert "NPM_CONFIG_REGISTRY=" in environment
    assert 'NPM_CONFIG_REGISTRY="${NPM_CONFIG_REGISTRY:-https://registry.npmmirror.com}"' in installer
    assert "npm install" not in installer


def test_deployment_restarts_display_and_doctor_uses_accessible_workdir() -> None:
    provision = (DEPLOY / "provision.sh").read_text(encoding="utf-8")
    doctor = (DEPLOY / "doctor.sh").read_text(encoding="utf-8")
    service = (DEPLOY / "auto-shipped-xvfb.service").read_text(encoding="utf-8")

    assert "systemctl restart auto-shipped-xvfb.service" in provision
    assert "xdpyinfo" in provision
    assert "ReadWritePaths=/var/lib/auto-shipped /tmp" in service
    assert "cd /" in doctor
