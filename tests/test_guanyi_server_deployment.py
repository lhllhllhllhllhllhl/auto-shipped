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
        DEPLOY / "guanyi-credential-helper-root.sh",
        DEPLOY / "guanyi-credential-helper.sh",
        DEPLOY / "set-guanyi-credentials.sh",
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
    assert "GUANYI_CREDENTIAL_HELPER=/usr/local/bin/guanyi-credential-helper" in environment
    assert "GUANYI_RESIDENT_SESSION_TTL_MS=31536000000" in environment
    assert "GUANYI_HUMAN_TAKEOVER_AVAILABLE=0" in environment


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
        "resolve-no-task",
        "prepare-retry",
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
    assert "GUANYI_CREDENTIAL_HELPER" in wrapper
    assert "GUANYI_RESIDENT_SESSION_TTL_MS" in wrapper
    assert "GUANYI_HUMAN_TAKEOVER_AVAILABLE" in wrapper


def test_server_credentials_are_encrypted_and_read_through_a_narrow_helper() -> None:
    provision = (DEPLOY / "provision.sh").read_text(encoding="utf-8")
    doctor = (DEPLOY / "doctor.sh").read_text(encoding="utf-8")
    setter = (DEPLOY / "set-guanyi-credentials.sh").read_text(encoding="utf-8")
    helper = (DEPLOY / "guanyi-credential-helper-root.sh").read_text(
        encoding="utf-8"
    )

    assert "openssl enc -aes-256-cbc -pbkdf2" in setter
    assert "openssl enc -d -aes-256-cbc -pbkdf2" in helper
    assert "NOPASSWD: /usr/local/libexec/auto-shipped-guanyi-credential-helper get" in provision
    assert "GUANYI_CREDENTIAL_HELPER" in doctor
    assert "GUANYI_LOGIN_PASSWORD" not in setter


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


def test_deployment_preserves_healthy_display_and_doctor_uses_accessible_workdir() -> None:
    provision = (DEPLOY / "provision.sh").read_text(encoding="utf-8")
    doctor = (DEPLOY / "doctor.sh").read_text(encoding="utf-8")
    service = (DEPLOY / "auto-shipped-xvfb.service").read_text(encoding="utf-8")

    assert "systemctl is-active --quiet auto-shipped-xvfb.service" in provision
    assert "systemctl restart auto-shipped-xvfb.service" not in provision
    assert "xdpyinfo" in provision
    assert "ReadWritePaths=/var/lib/auto-shipped /tmp" in service
    assert "cd /" in doctor
