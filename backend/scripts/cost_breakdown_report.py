"""One-off cost breakdown from llm_call_log (Supabase or SQLite)."""
from __future__ import annotations

from collections import defaultdict

from app.services import supabase_db as sb
from app.services.llm_call_log import _fetch_sqlite, ensure_llm_call_log_table
from app.services.rfp_repository import _connect


def fetch_all_rows() -> list[dict]:
    ensure_llm_call_log_table()
    if sb.use_supabase_db():
        client = sb._get_client()
        rows: list[dict] = []
        offset = 0
        page = 1000
        while True:
            result = (
                client.table("llm_call_log")
                .select(
                    "run_id,rfp_id,node_name,model,cost_usd,input_tokens,output_tokens,created_at"
                )
                .order("created_at")
                .range(offset, offset + page - 1)
                .execute()
            )
            batch = [dict(r) for r in (result.data or []) if isinstance(r, dict)]
            if not batch:
                break
            rows.extend(batch)
            if len(batch) < page:
                break
            offset += page
        return rows
    with _connect() as conn:
        conn.row_factory = None
        cur = conn.execute(
            """
            SELECT run_id, rfp_id, node_name, model, cost_usd, input_tokens, output_tokens,
                   cache_creation_input_tokens, cache_read_input_tokens, created_at
            FROM llm_call_log ORDER BY created_at
            """
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_rfp_titles() -> dict[str, str]:
    if not sb.use_supabase_db():
        with _connect() as conn:
            cur = conn.execute("SELECT id, title FROM rfps")
            return {str(r[0]): str(r[1] or "") for r in cur.fetchall()}
    client = sb._get_client()
    result = client.table("rfps").select("id,title").limit(1000).execute()
    return {str(r["id"]): str(r.get("title") or "") for r in (result.data or [])}


def main() -> None:
    rows = fetch_all_rows()
    titles = fetch_rfp_titles()
    print(f"source={'supabase' if sb.use_supabase_db() else 'sqlite'}")
    print(f"total_rows={len(rows)}")

    by_rfp: dict[str, dict] = defaultdict(
        lambda: {"cost": 0.0, "calls": 0, "in": 0, "out": 0, "runs": set()}
    )
    by_run: dict[str, dict] = defaultdict(
        lambda: {"cost": 0.0, "calls": 0, "rfp_id": "", "in": 0, "out": 0}
    )
    by_node: dict[str, dict] = defaultdict(lambda: {"cost": 0.0, "calls": 0})
    by_model: dict[str, dict] = defaultdict(lambda: {"cost": 0.0, "calls": 0})
    total = 0.0

    for row in rows:
        cost = float(row.get("cost_usd") or 0)
        total += cost
        rfp = str(row.get("rfp_id") or "unknown")
        run = str(row.get("run_id") or "unknown")
        node = str(row.get("node_name") or "unknown")
        model = str(row.get("model") or "unknown")
        inp = int(row.get("input_tokens") or 0)
        out = int(row.get("output_tokens") or 0)

        b = by_rfp[rfp]
        b["cost"] += cost
        b["calls"] += 1
        b["in"] += inp
        b["out"] += out
        b["runs"].add(run)

        br = by_run[run]
        br["cost"] += cost
        br["calls"] += 1
        br["rfp_id"] = rfp
        br["in"] += inp
        br["out"] += out

        by_node[node]["cost"] += cost
        by_node[node]["calls"] += 1
        by_model[model]["cost"] += cost
        by_model[model]["calls"] += 1

    print(f"TOTAL_USD=${total:.4f}")
    print("\n=== BY PROPOSAL (rfp_id) ===")
    for rfp, d in sorted(by_rfp.items(), key=lambda x: x[1]["cost"], reverse=True):
        title = titles.get(rfp, "")[:60]
        print(
            f"  {rfp} | {title!r} | ${d['cost']:.4f} | "
            f"{d['calls']} calls | {len(d['runs'])} runs | "
            f"in={d['in']} out={d['out']}"
        )

    print("\n=== BY RUN (each generate/scan session) ===")
    for run, d in sorted(by_run.items(), key=lambda x: x[1]["cost"], reverse=True):
        title = titles.get(d["rfp_id"], "")[:40]
        print(
            f"  {run[:8]}... | rfp={d['rfp_id'][:8]}... {title!r} | "
            f"${d['cost']:.4f} | {d['calls']} calls"
        )

    print("\n=== BY PIPELINE STAGE (node_name) ===")
    for node, d in sorted(by_node.items(), key=lambda x: x[1]["cost"], reverse=True):
        print(f"  {node}: ${d['cost']:.4f} ({d['calls']} calls)")

    print("\n=== BY MODEL ===")
    for model, d in sorted(by_model.items(), key=lambda x: x[1]["cost"], reverse=True):
        print(f"  {model}: ${d['cost']:.4f} ({d['calls']} calls)")


if __name__ == "__main__":
    main()
