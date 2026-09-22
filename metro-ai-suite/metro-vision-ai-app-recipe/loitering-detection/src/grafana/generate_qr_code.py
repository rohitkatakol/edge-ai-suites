#!/usr/bin/env python3
"""Embed a QR code linking to this app's GitHub landing page into the Grafana dashboard.

The QR code always points to the GitHub landing page of the branch/tag that is
currently checked out, so the demo links back to the exact sources it was run from.
"""
import argparse
import base64
import io
import json
import os
import subprocess

REPO_URL = "https://github.com/open-edge-platform/edge-ai-suites"
APP_PATH = "metro-ai-suite/metro-vision-ai-app-recipe/loitering-detection"
DEFAULT_BRANCH = "main"

DASHBOARD_PATH = os.path.join(
    os.path.dirname(__file__), "dashboards", "visualizer.json"
)


def get_repo_branch(repo_dir: str) -> str:
    """Return the current git branch/tag of repo_dir, falling back to DEFAULT_BRANCH."""
    try:
        branch = subprocess.check_output(
            ["git", "-C", repo_dir, "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        if branch and branch != "HEAD":
            return branch
        # Detached HEAD (e.g. a checked-out tag) - fall back to the tag name.
        tag = subprocess.check_output(
            ["git", "-C", repo_dir, "describe", "--tags", "--exact-match"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        return tag or DEFAULT_BRANCH
    except (subprocess.CalledProcessError, FileNotFoundError):
        return DEFAULT_BRANCH


def get_qr_code(text: str, size: int = 256):
    import qrcode
    from qrcode.image.styledpil import StyledPilImage
    from qrcode.image.styles.moduledrawers import GappedSquareModuleDrawer
    import PIL.Image

    qr = qrcode.QRCode(
        box_size=10, border=2, error_correction=qrcode.constants.ERROR_CORRECT_L
    )
    qr.add_data(text)
    img = qr.make_image(
        image_factory=StyledPilImage, module_drawer=GappedSquareModuleDrawer()
    )
    return img.resize((size, size), resample=PIL.Image.LANCZOS)


def to_base64_png(img) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def update_dashboard(dashboard_path: str, landing_page_url: str, qr_base64: str) -> None:
    with open(dashboard_path, "r", encoding="utf-8") as f:
        dashboard = json.load(f)

    for variable in dashboard["templating"]["list"]:
        if variable["name"] == "GITHUB_LANDING_PAGE_URL":
            variable["query"] = landing_page_url
            variable["current"] = {"text": landing_page_url, "value": landing_page_url}
            variable["options"] = [
                {"selected": True, "text": landing_page_url, "value": landing_page_url}
            ]
        elif variable["name"] == "QR_CODE_BASE64":
            variable["query"] = qr_base64
            variable["current"] = {"text": qr_base64, "value": qr_base64}
            variable["options"] = [
                {"selected": True, "text": qr_base64, "value": qr_base64}
            ]

    with open(dashboard_path, "w", encoding="utf-8") as f:
        json.dump(dashboard, f, indent=2)
        f.write("\n")


def main() -> None:
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..")
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=repo_root)
    parser.add_argument("--dashboard", default=DASHBOARD_PATH)
    parser.add_argument("--branch", help="Override auto-detected git branch/tag")
    args = parser.parse_args()

    branch = args.branch or os.environ.get("GIT_BRANCH") or get_repo_branch(args.repo_root)
    landing_page_url = f"{REPO_URL}/tree/{branch}/{APP_PATH}"

    qr_base64 = to_base64_png(get_qr_code(landing_page_url, size=200))
    update_dashboard(args.dashboard, landing_page_url, qr_base64)

    print(f"Embedded QR code linking to {landing_page_url} into {args.dashboard}")


if __name__ == "__main__":
    main()
