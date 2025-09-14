
import argparse
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)
    sys.path.insert(0, ROOT)

VENV_SP = os.path.join(ROOT, ".venv", "Lib", "site-packages")
if os.path.isdir(VENV_SP) and VENV_SP not in sys.path:
    sys.path.insert(0, VENV_SP)


from src.run import cmd_toc, cmd_validate, cmd_metrics, cmd_graph, cmd_kg, cmd_report
from src.run import cmd_chunk as _cmd_chunks


def main():
    ap = argparse.ArgumentParser(description="End-to-end PDF → JSON/JSONL pipeline")
    ap.add_argument("--pdf", required=True, help="Path to the USB PD PDF")
    ap.add_argument("--doc-title", default="Universal Serial Bus Power Delivery Specification",
                    help="Document title")
    ap.add_argument("--outdir", default=os.path.join("data", "output"), help="Output directory")
    ap.add_argument("--chunk-strategy", choices=["headings", "toc"], default="headings",
                    help="Chunking strategy (default: headings)")
    ap.add_argument("--toc-pages", default=None,
                    help="Optional ToC page range like '13-18' (else autodetect)")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)


    toc_path = os.path.join(args.outdir, "usb_pd_spec.jsonl")
    cmd_toc(SimpleNamespace(
        pdf=args.pdf,
        toc_pages=args.toc_pages,
        doc_title=args.doc_title,
        out=toc_path,
        min_dots=0,
        strip_dot_leaders=True
    ))
    print(f"[1/7] ToC → {toc_path}")

    chunks_path = os.path.join(args.outdir, "chunks.jsonl")
    _cmd_chunks(SimpleNamespace(pdf=args.pdf, toc=toc_path, out=chunks_path))
    print(f"[2/7] Chunks → {chunks_path}")

    validation_path = os.path.join(args.outdir, "validation.jsonl")
    cmd_validate(SimpleNamespace(toc=toc_path, chunks=chunks_path, out=validation_path))
    print(f"[3/7] Validation → {validation_path}")

    metrics_path = os.path.join(args.outdir, "metrics.jsonl")
    cmd_metrics(SimpleNamespace(toc=toc_path, chunks=chunks_path, out=metrics_path))
    print(f"[4/7] Metrics → {metrics_path}")

    toc_graph_path = os.path.join(args.outdir, "toc_graph.jsonl")
    cmd_graph(SimpleNamespace(toc=toc_path, out=toc_graph_path))
    print(f"[5/7] ToC Graph → {toc_graph_path}")

    triples_path = os.path.join(args.outdir, "triples.jsonl")
    cmd_kg(SimpleNamespace(
        chunks=chunks_path,
        out=triples_path,
        array=False,
        max_nodes=None
    ))
    print(f"[6/7] Triples → {triples_path}")

    report_path = os.path.join(args.outdir, "final_report.jsonl")
    cmd_report(SimpleNamespace(validation=validation_path, metrics=metrics_path, out=report_path))
    print(f"[7/7] Report → {report_path}")


if __name__ == "__main__":
    main()
