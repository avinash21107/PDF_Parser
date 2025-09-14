# USB PD Spec → ToC, Logical Chunking & Validation

A lean Python pipeline that:
- Parses Table of Contents (ToC) from the USB Power Delivery specification PDF
- Logically chunks the full document by numbered headings
- Detects figures/tables (robust captions: “Figure 5-1”, “Table 10-3”, multi-line)
- Validates ToC vs parsed chunks (missing / extra / out-of-order)
- Produces JSONL outputs (line-delimited JSON) + a validation JSON report

> Example target ToC rows (from the spec):  
> `1 Overview` … `1.2 Purpose` … `10 Power Rules`



---

## Setup

1. Clone and create environment:
```bash
git clone <repo-url>
cd PDFParser
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate
```
2. Install Dependency:
```
pip install -r requirements.txt
```
3.Extract ToC → JSONL:
```
python src/run.py toc \
  --pdf "data/input/USB_PD_R3_2 V1.1 2024-10 (1).pdf" \
  --out "data/output/usb_pd_spec.jsonl" \
  --doc-title "USB Power Delivery Specification, Rev 3.2, V1.1 (Oct 2024)"
```

4.Chunk Full Document → JSONL
```
python src/run.py chunk \
  --pdf "data/input/USB_PD_R3_2 V1.1 2024-10 (1).pdf" \
  --toc "data/output/usb_pd_spec.jsonl" \
  --out "data/output/chunks.jsonl"
```
5.Validate ToC vs Chunks → JSON Report
```
python src/run.py validate \
  --toc "data/output/usb_pd_spec.jsonl" \
  --chunks "data/output/chunks.jsonl" \
  --out "data/output/validation_report.json"
```
Project Structure:
```
PDFParser/
│
├── data/
│   ├── input/              # Place input PDFs here
│   └── output/             # Generated JSONL, graphs, metrics, validation
│
├── src/
│   ├── run.py              # Main entry point (CLI)
│   ├── toc.py              # Table of Contents (ToC) extraction
│   ├── chunk.py            # Chunk extraction & figure/table detection
│   ├── validate.py         # Validation scripts
│   ├── reports/            # Metrics & report generation
│   └── graph/              # ToC graph builder
│
├── requirements.txt
└── README.md
```

Sample Verified Output:
```
{
  "summary": "Matched 62 of 63 ToC sections (98.4% match).",
  "metrics": {
    "toc_sections": 63,
    "parsed_sections": 62,
    "figures": 1376,
    "tables": 0
  },
  "missing_sections": [
    "10 Universal Serial Bus Power Delivery Specification, Revision 3.2, Version 1.1, 2024-10-09 Page"
  ]
}


