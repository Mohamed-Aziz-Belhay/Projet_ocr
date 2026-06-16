from __future__ import annotations

import argparse
import json
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests


SUPPORTED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".pdf",
    ".tif",
    ".tiff",
    ".webp",
}


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def is_supported_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS


def wait_until_file_is_stable(path: Path, timeout_s: int = 30) -> bool:
    """
    Attend que le scanner termine l'écriture du fichier.
    Important pour éviter d'envoyer un PDF/JPG incomplet.
    """
    start = time.time()
    last_size = -1
    stable_count = 0

    while time.time() - start < timeout_s:
        if not path.exists():
            return False

        try:
            size = path.stat().st_size
        except OSError:
            time.sleep(1)
            continue

        if size > 0 and size == last_size:
            stable_count += 1
        else:
            stable_count = 0

        if stable_count >= 2:
            return True

        last_size = size
        time.sleep(1)

    return False


def safe_filename(path: Path) -> str:
    stem = path.stem.replace(" ", "_")
    suffix = path.suffix.lower()
    return f"{stem}_{now_stamp()}{suffix}"


def send_to_ocr(
    *,
    file_path: Path,
    api_url: str,
    api_key: str,
    bearer_token: Optional[str],
    document_type: str,
    template_id: Optional[str],
    engine: str,
    processing_mode: str,
    cin_mode: str,
    language_hint: Optional[str],
    include_diagnostics: bool,
) -> dict:
    headers = {
        "X-API-Key": api_key,
    }

    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    data = {
        "document_type": document_type,
        "engine": engine,
        "processing_mode": processing_mode,
        "cin_mode": cin_mode,
        "include_diagnostics": str(include_diagnostics).lower(),
    }

    if template_id:
        data["template_id"] = template_id

    if language_hint:
        data["language_hint"] = language_hint

    with file_path.open("rb") as f:
        files = {
            "file": (file_path.name, f, "application/octet-stream"),
        }

        response = requests.post(
            api_url,
            headers=headers,
            data=data,
            files=files,
            timeout=300,
        )

    try:
        payload = response.json()
    except Exception:
        payload = {
            "status_code": response.status_code,
            "text": response.text,
        }

    if response.status_code >= 400:
        raise RuntimeError(json.dumps(payload, ensure_ascii=False, indent=2))

    return payload


def process_file(
    *,
    path: Path,
    processing_dir: Path,
    done_dir: Path,
    error_dir: Path,
    output_dir: Path,
    api_url: str,
    api_key: str,
    bearer_token: Optional[str],
    document_type: str,
    template_id: Optional[str],
    engine: str,
    processing_mode: str,
    cin_mode: str,
    language_hint: Optional[str],
    include_diagnostics: bool,
) -> None:
    print(f"[SCAN] Nouveau fichier détecté: {path}")

    if not wait_until_file_is_stable(path):
        print(f"[WARN] Fichier instable ou inaccessible: {path}")
        return

    processing_dir.mkdir(parents=True, exist_ok=True)
    done_dir.mkdir(parents=True, exist_ok=True)
    error_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    work_name = safe_filename(path)
    work_path = processing_dir / work_name

    try:
        shutil.move(str(path), str(work_path))

        print(f"[OCR] Envoi vers API: {work_path.name}")

        result = send_to_ocr(
            file_path=work_path,
            api_url=api_url,
            api_key=api_key,
            bearer_token=bearer_token,
            document_type=document_type,
            template_id=template_id,
            engine=engine,
            processing_mode=processing_mode,
            cin_mode=cin_mode,
            language_hint=language_hint,
            include_diagnostics=include_diagnostics,
        )

        result_path = output_dir / f"{work_path.stem}.json"
        result_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        final_path = done_dir / work_path.name
        shutil.move(str(work_path), str(final_path))

        print(f"[OK] OCR terminé")
        print(f"     Fichier: {final_path}")
        print(f"     JSON:    {result_path}")

    except Exception as exc:
        print(f"[ERROR] Échec OCR pour {path}: {exc}")

        error_json = {
            "file": str(path),
            "error": str(exc),
            "failed_at": datetime.now().isoformat(),
        }

        error_path = output_dir / f"error_{now_stamp()}_{path.stem}.json"
        error_path.write_text(
            json.dumps(error_json, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if work_path.exists():
            shutil.move(str(work_path), str(error_dir / work_path.name))


def watch_folder(
    *,
    input_dir: Path,
    processing_dir: Path,
    done_dir: Path,
    error_dir: Path,
    output_dir: Path,
    api_url: str,
    api_key: str,
    bearer_token: Optional[str],
    document_type: str,
    template_id: Optional[str],
    engine: str,
    processing_mode: str,
    cin_mode: str,
    language_hint: Optional[str],
    include_diagnostics: bool,
    interval_s: float,
) -> None:
    input_dir.mkdir(parents=True, exist_ok=True)
    processing_dir.mkdir(parents=True, exist_ok=True)
    done_dir.mkdir(parents=True, exist_ok=True)
    error_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()

    print("[START] Scanner Agent démarré")
    print(f"[WATCH] Dossier surveillé: {input_dir}")
    print(f"[API]   {api_url}")
    print("[INFO] Pour tester sans scanner, copiez un PDF/JPG dans le dossier IN.")
    print("")

    while True:
        try:
            files = [
                p for p in sorted(input_dir.iterdir())
                if is_supported_file(p)
            ]

            for path in files:
                key = str(path.resolve())

                if key in seen:
                    continue

                seen.add(key)

                process_file(
                    path=path,
                    processing_dir=processing_dir,
                    done_dir=done_dir,
                    error_dir=error_dir,
                    output_dir=output_dir,
                    api_url=api_url,
                    api_key=api_key,
                    bearer_token=bearer_token,
                    document_type=document_type,
                    template_id=template_id,
                    engine=engine,
                    processing_mode=processing_mode,
                    cin_mode=cin_mode,
                    language_hint=language_hint,
                    include_diagnostics=include_diagnostics,
                )

            time.sleep(interval_s)

        except KeyboardInterrupt:
            print("\n[STOP] Scanner Agent arrêté")
            break

        except Exception as exc:
            print(f"[ERROR] Boucle agent: {exc}")
            time.sleep(interval_s)


def main() -> None:
    parser = argparse.ArgumentParser(description="OCR Scanner Agent")

    parser.add_argument("--base-dir", default=r"C:\OCR_SCANS")
    parser.add_argument("--api-url", default="http://localhost:8000/extract")
    parser.add_argument("--api-key", default="dev-key-123")
    parser.add_argument("--bearer-token", default=None)

    parser.add_argument("--document-type", default="auto")
    parser.add_argument("--template-id", default=None)
    parser.add_argument("--engine", default="auto")
    parser.add_argument("--processing-mode", default="balanced")
    parser.add_argument("--cin-mode", default="balanced")
    parser.add_argument("--language-hint", default=None)
    parser.add_argument("--include-diagnostics", action="store_true", default=True)

    parser.add_argument("--interval", type=float, default=2.0)

    args = parser.parse_args()

    base_dir = Path(args.base_dir)

    watch_folder(
        input_dir=base_dir / "IN",
        processing_dir=base_dir / "PROCESSING",
        done_dir=base_dir / "DONE",
        error_dir=base_dir / "ERROR",
        output_dir=base_dir / "OUT",
        api_url=args.api_url,
        api_key=args.api_key,
        bearer_token=args.bearer_token,
        document_type=args.document_type,
        template_id=args.template_id,
        engine=args.engine,
        processing_mode=args.processing_mode,
        cin_mode=args.cin_mode,
        language_hint=args.language_hint,
        include_diagnostics=args.include_diagnostics,
        interval_s=args.interval,
    )


if __name__ == "__main__":
    main()