#!/usr/bin/env python3
import argparse, os, sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.abspath(__file__))
VENV_SP = os.path.join(ROOT, ".venv", "Lib", "site-packages")
if os.path.isdir(VENV_SP) and VENV_SP not in sys.path:
    sys.path.insert(0, VENV_SP)

SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)
    sys.path.insert(0, ROOT)

from src.run import (
    cmd_toc, cmd_chunk, cmd_validate, cmd_metrics,
    cmd_graph, cmd_kg, cmd_report
)


def main():
    ap = argparse.ArgumentParser(description="End-to-end PDF → JSONL pipeline")
    ap.add_argument("--pdf", required=True, help="Path to the USB PD PDF")
    ap.add_argument("--doc-title", default="USB PD Specification", help="Document title")
    ap.add_argument("--outdir", default="data/output", help="Output directory")
    ap.add_argument("--chunk-strategy", choices=["headings", "toc"], default="headings")
    ap.add_argument("--toc-pages", default=None, help="Optional page range, e.g. 2-6")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)

    # 1) ToC
    toc_path = os.path.join(args.outdir, "usb_pd_spec.jsonl")
    cmd_toc(SimpleNamespace(pdf=args.pdf, toc_pages=args.toc_pages,
                            doc_title=args.doc_title, out=toc_path))
    print(f"[1/7] ToC → {toc_path}")

    # 2) Chunks
    chunks_path = os.path.join(args.outdir, "chunks.jsonl")
    cmd_chunk(SimpleNamespace(pdf=args.pdf, strategy=args.chunk_strategy,
                               doc_title=args.doc_title, out=chunks_path))
    print(f"[2/7] Chunks → {chunks_path}")

    # 3) Validate
    validation_path = os.path.join(args.outdir, "validation.json")
    cmd_validate(SimpleNamespace(toc=toc_path, chunks=chunks_path, out=validation_path))
    print(f"[3/7] Validation → {validation_path}")

    # 4) Metrics
    metrics_path = os.path.join(args.outdir, "metrics.json")
    cmd_metrics(SimpleNamespace(chunks=chunks_path, out=metrics_path))
    print(f"[4/7] Metrics → {metrics_path}")

    # 5) ToC Graph
    toc_graph_path = os.path.join(args.outdir, "toc_graph.json")
    cmd_graph(SimpleNamespace(toc=toc_path, out=toc_graph_path))
    print(f"[5/7] ToC Graph → {toc_graph_path}")

    # 6) Knowledge Triples (optional but nice)
    triples_path = os.path.join(args.outdir, "triples.jsonl")
    cmd_kg(SimpleNamespace(chunks=chunks_path, out=triples_path))
    print(f"[6/7] Triples → {triples_path}")

    # 7) Final report (JSONL)
    report_path = os.path.join(args.outdir, "final_report.jsonl")
    cmd_report(SimpleNamespace(validation=validation_path,
                               metrics=metrics_path,
                               out=report_path))
    print(f"[7/7] Report → {report_path}")

if __name__ == "__main__":
    main()
