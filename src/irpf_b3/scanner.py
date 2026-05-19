import csv
import json
import os
import re
import time
from pathlib import Path
from pydantic import BaseModel

from irpf_b3.config import settings
from irpf_b3.helpers import extract_keyword_context, progress
from irpf_b3.classifier import call_ollama

_CORPORATE_EVENT_PATTERN = re.compile(
    settings.corporate_event_pattern_str, re.IGNORECASE
)

_TRIAGE_SYSTEM_PROMPT = """You are a financial document triage assistant.
You receive a short excerpt from a B3 (Brazilian Stock Exchange) corporate document.
Determine if this excerpt indicates an actual corporate event deliberation or approval 
(stock bonus/bonificação, stock split/desdobramento, reverse split/grupamento, subscription rights).

Rules:
1. Respond ONLY with: YES, MAYBE, or NO.
2. YES = The excerpt describes an approved or deliberated corporate event.
3. MAYBE = The excerpt hints at a possible event but is not conclusive.
4. NO = The excerpt mentions keywords in a boilerplate/historical/unrelated context.
"""

_TRIAGE_USER_TEMPLATE = """Excerpt from {category}/{filename}:
---
{snippet_text}
---
Decision (YES, MAYBE, or NO):"""

_TRIAGE_VALID_TAGS = ["YES", "MAYBE", "NO"]

_ANALYSIS_SYSTEM_PROMPT = """You are a corporate event analyst for B3 (Brazilian Stock Exchange) listed companies.
Analyze the full document text and extract structured information about corporate events.

You MUST respond in valid JSON format with exactly these fields:
{
  "event_type": "BONUS | SPLIT | REVERSE_SPLIT | SUBSCRIPTION | OTHER | NONE",
  "event_date": "YYYY-MM-DD or null if not found",
  "approval_date": "YYYY-MM-DD or null",
  "record_date": "YYYY-MM-DD or null (data-base/data de corte)",
  "ratio": "string describing the ratio, e.g. '1:10' or '20%' or null",
  "shares_before": "number or null",
  "shares_after": "number or null",
  "summary": "One-paragraph description of the event in English (max 200 words)",
  "confidence": "HIGH | MEDIUM | LOW"
}

Rules:
1. If multiple events are mentioned, report only the PRIMARY event.
2. Dates must be in ISO format (YYYY-MM-DD).
3. If a field cannot be determined, set it to null.
4. The summary must be factual and concise."""

_ANALYSIS_USER_TEMPLATE = """Full document from {ticker} - {category}/{filename}:
---
{full_text}
---
JSON analysis:"""


class ScanHit(BaseModel):
    ticker: str
    category: str
    filename: str
    filepath: str
    snippets: list[str]


class CorporateEventReport(BaseModel):
    ticker: str
    category: str
    filename: str
    filepath: str
    event_type: str | None = None
    event_date: str | None = None
    approval_date: str | None = None
    record_date: str | None = None
    ratio: str | None = None
    shares_before: str | None = None
    shares_after: str | None = None
    summary: str | None = None
    confidence: str | None = None
    triage_result: str = ""
    raw_llm_response: str = ""


def scan_documents_for_events(target_dir: str) -> list[ScanHit]:
    """Walk ticker directory, filter by category, extract keyword context.

    Returns:
        List of ScanHit with file metadata and extracted paragraph snippets.
    """
    target_path = Path(target_dir)
    if not target_path.exists():
        print(f"Error: The directory '{target_dir}' does not exist.")
        return []

    matched_hits: list[ScanHit] = []
    total_files = 0
    skipped_by_category = 0

    print(f"Starting scan in: {target_path}")
    print(f"Allowed categories: {sorted(settings.allowed_categories)}")
    print("-" * 60)

    for root, _, files in os.walk(target_path):
        for file in files:
            if not file.endswith(".txt"):
                continue

            total_files += 1
            filepath = Path(root) / file
            category = filepath.parent.name

            if not settings.b3_allow_all_categories and category not in settings.allowed_categories:
                skipped_by_category += 1
                continue

            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()

                snippets = extract_keyword_context(
                    content,
                    _CORPORATE_EVENT_PATTERN,
                    settings.corporate_event_context_chars
                )

                if snippets:
                    # Infer ticker from grandparent directory (docs/pdf/TICKER/category/file.txt)
                    ticker = filepath.parent.parent.name
                    matched_hits.append(
                        ScanHit(
                            ticker=ticker,
                            category=category,
                            filename=file,
                            filepath=str(filepath),
                            snippets=snippets,
                        )
                    )
            except Exception as e:
                print(f"Error reading {filepath}: {e}")

    print("-" * 60)
    print(
        f"Scan completed! {len(matched_hits)} files matched regex "
        f"out of {total_files} .txt files "
        f"({skipped_by_category} skipped by category)."
    )

    return matched_hits


def triage_scan_hits(scan_hits: list[ScanHit], output_csv: str) -> list[ScanHit]:
    """Run LLM triage on each ScanHit's snippets.

    Returns only hits classified as YES or MAYBE.
    Writes incremental CSV for idempotent reruns.
    """
    already_processed: dict[str, str] = {}
    if os.path.exists(output_csv) and os.path.getsize(output_csv) > 0:
        try:
            with open(output_csv, "r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    already_processed[row.get("filename", "")] = row.get("result", "")
        except Exception as e:
            print(f"Warning loading existing triage CSV: {e}")

    csv_fieldnames = ["result", "ticker", "category", "filename", "filepath"]
    csv_is_new = not os.path.exists(output_csv) or os.path.getsize(output_csv) == 0

    triaged_hits: list[ScanHit] = []
    
    pending = [hit for hit in scan_hits if hit.filename not in already_processed]
    print(f"\n[Triage] {len(already_processed)} already triaged, {len(pending)} pending.")

    triage_start_time = time.time()
    with open(output_csv, "a", encoding="utf-8", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=csv_fieldnames)
        if csv_is_new:
            writer.writeheader()

        for i, hit in enumerate(pending, 1):
            combined_snippets = "\n---\n".join(hit.snippets)[:2000]
            
            user_prompt = _TRIAGE_USER_TEMPLATE.format(
                category=hit.category,
                filename=hit.filename,
                snippet_text=combined_snippets
            )
            
            start_time = time.time()
            decision = call_ollama(
                system_prompt=_TRIAGE_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                valid_tags=_TRIAGE_VALID_TAGS,
                timeout=settings.llm_triage_timeout
            )
            prog_str = progress(i, len(pending), triage_start_time)
            print(f"{prog_str} [{decision}] {hit.category}/{hit.filename}")
            
            writer.writerow(
                {
                    "result": decision,
                    "ticker": hit.ticker,
                    "category": hit.category,
                    "filename": hit.filename,
                    "filepath": hit.filepath,
                }
            )
            out.flush()
            already_processed[hit.filename] = decision

    # Filter all hits based on already_processed dictionary
    for hit in scan_hits:
        decision = already_processed.get(hit.filename, "")
        if decision in ["YES", "MAYBE"]:
            triaged_hits.append(hit)

    return triaged_hits


def analyze_corporate_events(
    triaged_hits: list[ScanHit],
    triage_results: dict[str, str],
    output_csv: str
) -> list[CorporateEventReport]:
    """Run deep LLM analysis on each triaged document.

    Returns structured CorporateEventReport for each document.
    Writes incremental CSV for idempotent reruns.
    """
    already_processed_files = set()
    if os.path.exists(output_csv) and os.path.getsize(output_csv) > 0:
        try:
            with open(output_csv, "r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    already_processed_files.add(row.get("filename", ""))
        except Exception as e:
            print(f"Warning loading existing analysis CSV: {e}")

    csv_fieldnames = list(CorporateEventReport.model_fields.keys())
    csv_is_new = not os.path.exists(output_csv) or os.path.getsize(output_csv) == 0

    pending = [hit for hit in triaged_hits if hit.filename not in already_processed_files]
    print(f"\n[Analysis] {len(already_processed_files)} already analyzed, {len(pending)} pending.")

    reports: list[CorporateEventReport] = []

    analysis_start_time = time.time()
    with open(output_csv, "a", encoding="utf-8", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=csv_fieldnames)
        if csv_is_new:
            writer.writeheader()

        for i, hit in enumerate(pending, 1):
            try:
                with open(hit.filepath, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            except Exception as e:
                print(f"[{i}/{len(pending)}] Error reading {hit.filepath}: {e}")
                continue

            full_text = text[:settings.llm_max_text_length]
            user_prompt = _ANALYSIS_USER_TEMPLATE.format(
                ticker=hit.ticker,
                category=hit.category,
                filename=hit.filename,
                full_text=full_text
            )

            prog_str = progress(i, len(pending), analysis_start_time)
            print(f"[Analysis] {prog_str} {hit.category}/{hit.filename}...", end="", flush=True)
            start_time = time.time()
            
            raw_response = call_ollama(
                system_prompt=_ANALYSIS_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                valid_tags=None,
                timeout=settings.llm_analysis_timeout
            )
            elapsed = time.time() - start_time
            print(f" Done ({elapsed:.1f}s)")
            
            triage_decision = triage_results.get(hit.filename, "")

            # Attempt to parse JSON
            parsed_data = {}
            try:
                json_str = raw_response
                if "```json" in json_str:
                    json_str = json_str.split("```json")[1].split("```")[0]
                elif "```" in json_str:
                    json_str = json_str.split("```")[1].split("```")[0]
                    
                # Sometimes LLMs put extra text before the first '{' or after '}'
                start_idx = json_str.find("{")
                end_idx = json_str.rfind("}")
                if start_idx != -1 and end_idx != -1:
                    json_str = json_str[start_idx:end_idx+1]
                    
                parsed_data = json.loads(json_str.strip())
            except Exception as e:
                print(f"  -> JSON parse error: {e}")
                parsed_data = {"event_type": "PARSE_ERROR"}

            report = CorporateEventReport(
                ticker=hit.ticker,
                category=hit.category,
                filename=hit.filename,
                filepath=hit.filepath,
                event_type=str(parsed_data.get("event_type", "")),
                event_date=str(parsed_data.get("event_date", "")),
                approval_date=str(parsed_data.get("approval_date", "")),
                record_date=str(parsed_data.get("record_date", "")),
                ratio=str(parsed_data.get("ratio", "")),
                shares_before=str(parsed_data.get("shares_before", "")),
                shares_after=str(parsed_data.get("shares_after", "")),
                summary=str(parsed_data.get("summary", "")),
                confidence=str(parsed_data.get("confidence", "")),
                triage_result=triage_decision,
                raw_llm_response=raw_response,
            )
            
            reports.append(report)
            writer.writerow(report.model_dump())
            out.flush()
            
    # Load all processed events to return the complete list
    all_reports = []
    if os.path.exists(output_csv):
        with open(output_csv, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                all_reports.append(CorporateEventReport(**row))

    return all_reports


def run_pipeline(tickers: list[str], base_dir: str):
    """Orchestrates the 3-stage corporate event scanning pipeline for given tickers."""
    print(f"\n--- Starting Corporate Event Scan Pipeline for {len(tickers)} tickers ---")
    
    all_reports: list[CorporateEventReport] = []
    
    for ticker in tickers:
        ticker_dir = os.path.join(base_dir, ticker)
        if not os.path.exists(ticker_dir):
            print(f"Directory {ticker_dir} not found for {ticker}. Skipping.")
            continue
            
        print(f"\n[{ticker}] Stage 3.1: Regex + Paragraph Extraction")
        scan_hits = scan_documents_for_events(ticker_dir)
        if not scan_hits:
            continue
            
        print(f"\n[{ticker}] Stage 3.2: LLM Triage")
        triage_csv = os.path.join(ticker_dir, "scan_triage.csv")
        triaged_hits = triage_scan_hits(scan_hits, triage_csv)
        if not triaged_hits:
            continue
            
        # Re-build the triage results map for analysis module
        triage_results = {}
        if os.path.exists(triage_csv):
            with open(triage_csv, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    triage_results[row.get("filename", "")] = row.get("result", "")
                    
        print(f"\n[{ticker}] Stage 3.3: LLM Deep Analysis")
        analysis_csv = os.path.join(ticker_dir, "corporate_events_report.csv")
        reports = analyze_corporate_events(triaged_hits, triage_results, analysis_csv)
        all_reports.extend(reports)
        
    consolidated_json = os.path.join(base_dir, "corporate_events_consolidated.json")
    try:
        # Include both YES and MAYBE as per user request
        data_to_save = [r.model_dump() for r in all_reports if r.triage_result in ("YES", "MAYBE")]
        with open(consolidated_json, "w", encoding="utf-8") as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=2)
        print(f"\nConsolidated JSON saved to {consolidated_json} ({len(data_to_save)} events).")
    except Exception as e:
        print(f"\nFailed to save consolidated JSON: {e}")
