"""Scan local document tree for corporate events and classify with LLM.

Pipeline: filesystem walk → regex filter → category filter → LLM classification.
WHY category filter exists: 247/407 hits came from 'dados_economico_financeiros'
which are balance sheets mentioning "split" in historical footnotes — pure noise.
Filtering by category reduces LLM calls from ~407 to ~105, saving compute.
"""

import csv
import os
import re
from pathlib import Path
import argparse
import time

from irpf_b3.legacy.llm_client import classify_corporate_event

DEFAULT_TICKER = "WEGE3"

# Regex pattern: stem-based to catch singular/plural and accent variants
CORPORATE_EVENT_PATTERN = re.compile(
    r"bonifica|desdobrament|agrupament|subscri|split|inplit|fraç|frac",
    re.IGNORECASE,
)

# WHY: Deterministic filter eliminates categories that only mention events
# in boilerplate context (governance charters, internal policies, financial statements).
# Categories below are the ones where actual event deliberation/approval occurs.
HIGH_RELEVANCE_CATEGORIES = {
    "assembleia",
    "aviso_aos_acionistas",
    "comunicado_ao_mercado",
    "fato_relevante",
    "reuniao_da_administracao",
    "valores_mobiliarios_negociados_e_detidos",
    "relatorio_proventos",
}

MEDIUM_RELEVANCE_CATEGORIES = {
    "estatuto_social",
    "documentos_de_oferta_de_distribuicao_publica",
}

ALLOWED_CATEGORIES = HIGH_RELEVANCE_CATEGORIES | MEDIUM_RELEVANCE_CATEGORIES

CSV_FIELDNAMES = [
    "result",
    "ticker",
    "category",
    "filename",
    "filepath",
]


def scan_corporate_events(target_dir: str, extensions: tuple = (".txt",)) -> list[dict]:
    """Scan directory tree for documents matching corporate event keywords.

    Returns list of dicts with file metadata for downstream classification.
    """
    target_path = Path(target_dir)
    if not target_path.exists():
        print(f"Error: The directory '{target_dir}' does not exist.")
        return []

    matched_files: list[dict] = []
    total_files = 0
    skipped_by_category = 0

    print(f"Starting scan in: {target_path}")
    print(f"Extensions: {extensions}")
    print(f"Allowed categories: {sorted(ALLOWED_CATEGORIES)}")
    print("-" * 60)

    for root, _, files in os.walk(target_path):
        for file in files:
            if not file.endswith(extensions):
                continue

            total_files += 1
            filepath = Path(root) / file

            # Extract category from directory structure: docs/pdf/{TICKER}/{category}/file.txt
            category = filepath.parent.name

            if category not in ALLOWED_CATEGORIES:
                skipped_by_category += 1
                continue

            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()

                if CORPORATE_EVENT_PATTERN.search(content):
                    # Infer ticker from grandparent directory
                    ticker = filepath.parent.parent.name

                    matched_files.append(
                        {
                            "ticker": ticker,
                            "category": category,
                            "filename": file,
                            "filepath": str(filepath),
                        }
                    )
                    print(f"[MATCH] {category}/{file}")

            except Exception as e:
                print(f"Error reading {filepath}: {e}")

    print("-" * 60)
    print(
        f"Scan completed! {len(matched_files)} files found "
        f"out of {total_files} total files "
        f"({skipped_by_category} skipped by category)."
    )

    return matched_files


def classify_matched_files(matched_files: list[dict], output_csv: str) -> None:
    """Run LLM classification on each matched file and write results to CSV.

    Uses incremental append to survive interruptions.
    """
    # Load already-processed filenames to support idempotent reruns
    already_processed: set[str] = set()
    if os.path.exists(output_csv) and os.path.getsize(output_csv) > 0:
        try:
            with open(output_csv, "r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    already_processed.add(row.get("filename", ""))
        except Exception as e:
            print(f"Warning loading existing CSV: {e}")

    pending = [m for m in matched_files if m["filename"] not in already_processed]

    print(f"\n{len(already_processed)} already evaluated, {len(pending)} pending.")
    if not pending:
        print("Nothing to process.")
        return

    csv_is_new = not os.path.exists(output_csv) or os.path.getsize(output_csv) == 0

    with open(output_csv, "a", encoding="utf-8", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=CSV_FIELDNAMES)
        if csv_is_new:
            writer.writeheader()

        start_time = time.time()
        for i, item in enumerate(pending, 1):
            filepath = item["filepath"]
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            except Exception as e:
                print(f"[{i}/{len(pending)}] Error reading {filepath}: {e}")
                continue

            if not text.strip():
                print(f"[{i}/{len(pending)}] Empty text: {item['filename']}")
                continue

            decision = classify_corporate_event(text)
            elapsed = time.time() - start_time
            avg_time = elapsed / i
            remaining = len(pending) - i
            eta_secs = int(avg_time * remaining)
            eta_str = (
                f"{eta_secs // 60}m {eta_secs % 60}s"
                if eta_secs >= 60
                else f"{eta_secs}s"
            )
            print(
                f"[{decision}] [{i}/{len(pending)} {eta_str}] "
                f"{item['category']}/{item['filename']}"
            )

            writer.writerow(
                {
                    "result": decision,
                    "ticker": item["ticker"],
                    "category": item["category"],
                    "filename": item["filename"],
                    "filepath": filepath,
                }
            )
            out.flush()

    print(f"\nClassification completed. Results in: {output_csv}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scans and classifies corporate event documents."
    )
    parser.add_argument(
        "--dir",
        type=str,
        default=None,
        help="Root directory for the search (default: docs/pdf if --ticker is not provided)",
    )
    parser.add_argument(
        "--ticker",
        type=str,
        default=None,
        help=f"Specific company ticker (e.g.: {DEFAULT_TICKER})",
    )
    parser.add_argument(
        "--no-classify",
        dest="classify",
        action="store_false",
        help="Disable sending found files to the LLM for classification.",
    )
    parser.set_defaults(classify=True)
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Path to the results CSV. Default: docs/pdf/scan_results.csv",
    )

    args = parser.parse_args()

    if args.ticker:
        target_dir = os.path.join("docs", "pdf", args.ticker.upper())
    elif args.dir:
        target_dir = args.dir
    else:
        target_dir = os.path.join("docs", "pdf")

    matched_files = scan_corporate_events(target_dir)

    if args.classify and matched_files:
        output_csv = args.output or os.path.join("docs", "pdf", "scan_results.csv")
        classify_matched_files(matched_files, output_csv)


if __name__ == "__main__":
    main()
