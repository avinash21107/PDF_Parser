# USB PD Spec → ToC, Logical Chunking & Validation

A lean Python pipeline that:
- Parses Table of Contents (ToC) from the USB Power Delivery specification PDF
- Logically chunks the full document by numbered headings
- Detects figures/tables (robust captions: “Figure 5-1”, “Table 10-3”, multi-line)
- Validates ToC vs parsed chunks (missing / extra / out-of-order)
- Produces JSONL outputs (line-delimited JSON) + a validation JSON report

> Example target ToC rows (from the spec):  
> `1 Overview` … `1.2 Purpose` … `10 Power Rules`
>
> 

- ##Features:
- **Table of Contents Parsing** – Extract and validate hierarchical ToC entries.

- **Chunk Extraction** – Break down PDF into text chunks with structure awareness.

- **Validation** – Match ToC with parsed sections and generate structured validation reports.

- **Triple Writing Utilities** – Save extracted triples in both .json and .jsonl formats.

- **Error Handling** – Safe parsing with try/except blocks for reliability.

- **OOP Design** – Refactored to follow classes, wrappers, and modular principles.

---

## Setup and Installation:

```bash
git clone https://github.com/yourusername/PDFParser.git
cd PDFParser
python -m venv .venv
.venv\Scripts\activate   # Windows
source .venv/bin/activate # Linux/Mac
pip install -r requirements.txt
```
One shot pipeline
```python orchestrate.py --pdf "data/input/USB_PD.pdf" --outdir data/output```

Step-by-Step:
```
# 1) Extract ToC
python -m src.run toc --pdf "data/input/USB_PD.pdf" \
  --out "data/output/usb_pd_spec.jsonl" \
  --doc-title "Universal Serial Bus Power Delivery Specification" \
  --strip-dot-leaders

# 2) Chunk
python -m src.run chunk --pdf "data/input/USB_PD.pdf" \
  --toc "data/output/usb_pd_spec.jsonl" \
  --out "data/output/chunks.jsonl"

# 3) Validate
python -m src.run validate --toc "data/output/usb_pd_spec.jsonl" \
  --chunks "data/output/chunks.jsonl" \
  --out "data/output/validation.json"

# 4) Metrics
python -m src.run metrics --toc "data/output/usb_pd_spec.jsonl" \
  --chunks "data/output/chunks.jsonl" \
  --out "data/output/metrics.json"

# 5) ToC Graph
python -m src.run toc-graph --toc "data/output/usb_pd_spec.jsonl" \
  --out "data/output/toc_graph.json"

# 6) Knowledge Graph (Triples)
python -m src.run kg --chunks "data/output/chunks.jsonl" \
  --out "data/output/triples.jsonl"

# 7) Final Report
python -m src.run report --validation "data/output/validation.json" \
  --metrics "data/output/metrics.json" \
  --out "data/output/final_report.jsonl"
```
Output:
```
-usb_pd_spec.jsonl → ToC entries

-chunks.jsonl → Document chunks

-validation.json → Validation report

-metrics.json → Metrics (sections, figures, tables)

-toc_graph.json → ToC as graph

-triples.jsonl → Extracted triples

-final_report.jsonl → Combined QA report

```

Example JSONL(ToC Row)
```{
  "doc_title": "USB Power Delivery Specification Rev X",
  "section_id": "2.1.2",
  "title": "Power Delivery Contract Negotiation",
  "page": 53,
  "level": 3,
  "parent_id": "2.1",
  "full_path": "2.1.2 Power Delivery Contract Negotiation"
}
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








