import os
import sqlite3


# Each table gets a human-readable label and a query.
# We dump each table as formatted text so it can be chunked + embedded.
TABLE_CONFIGS = {
    "projects": {
        "label": "Projects",
        "query": """
            SELECT project_id, name, dept, lead, status,
                   budget, spent, start_date, end_date
            FROM projects
        """,
        "row_template": (
            "Project {project_id}: {name} | Dept: {dept} | Lead: {lead} | "
            "Status: {status} | Budget: {budget} | Spent: {spent} | "
            "Start: {start_date} | End: {end_date}"
        )
    },
    "audit_records": {
        "label": "Audit Records",
        "query": """
            SELECT audit_id, project_id, plant, date, auditor,
                   finding, status, resolution_date
            FROM audit_records
        """,
        "row_template": (
            "Audit {audit_id} | Project: {project_id} | Plant: {plant} | "
            "Date: {date} | Auditor: {auditor} | Finding: {finding} | "
            "Status: {status} | Resolved: {resolution_date}"
        )
    },
    "vendor_invoices": {
        "label": "Vendor Invoices",
        "query": """
            SELECT invoice_no, vendor_id, vendor_name, amount, date,
                   status, approved_by
            FROM vendor_invoices
        """,
        "row_template": (
            "Invoice {invoice_no} | Vendor: {vendor_name} ({vendor_id}) | "
            "Amount: {amount} | Date: {date} | Status: {status} | "
            "Approved by: {approved_by}"
        )
    },
    "production_log": {
        "label": "Production Log",
        "query": """
            SELECT id, month, plant, product_line,
                   units_produced, units_rejected, defect_rate_pct
            FROM production_log
        """,
        "row_template": (
            "Production [{month}] Plant: {plant} | Line: {product_line} | "
            "Produced: {units_produced} | Rejected: {units_rejected} | "
            "Defect rate: {defect_rate_pct}%"
        )
    },
    "employees": {
        "label": "Employees",
        "query": "SELECT emp_id, name, role, dept, email, location, joined FROM employees",
        "row_template": (
            "Employee {emp_id}: {name} | Role: {role} | Dept: {dept} | "
            "Email: {email} | Location: {location} | Joined: {joined}"
        )
    },
}


def extract_sql(db_path):
    """
    Read all configured tables from the SQLite DB.
    Returns one doc per table (so each table gets chunked independently).
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    fname = os.path.basename(db_path)
    docs  = []

    for table_name, cfg in TABLE_CONFIGS.items():
        try:
            cur.execute(cfg["query"].strip())
            rows = cur.fetchall()
            if not rows:
                continue

            lines = [f"=== {cfg['label']} ({len(rows)} records) ==="]
            for row in rows:
                row_dict = {k: (row[k] if row[k] is not None else "N/A")
                            for k in row.keys()}
                try:
                    lines.append(cfg["row_template"].format(**row_dict))
                except KeyError:
                    # Fallback: just join all values
                    lines.append(" | ".join(str(v) for v in row_dict.values()))

            docs.append({
                "text": "\n".join(lines),
                "metadata": {
                    "source":      fname,
                    "source_type": "sql",
                    "table":       table_name,
                    "sheet":       table_name,   # reuse sheet field for indexer compat
                    "file_path":   db_path,
                    "row_count":   len(rows),
                }
            })
        except Exception as e:
            print(f"  [ERROR] table {table_name}: {e}")

    conn.close()
    return docs


if __name__ == "__main__":
    import config, os
    db_path = os.path.join(config.SQL_DIR, "hawkins.db")
    docs = extract_sql(db_path)
    print(f"Extracted {len(docs)} table docs from {db_path}\n")
    for d in docs:
        print(f"Table: {d['metadata']['table']} | Rows: {d['metadata']['row_count']}")
        print(f"Preview: {d['text'][:200]}\n")