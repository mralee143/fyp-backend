You may query exactly three relations. They already contain ONLY the current
user's data — never add a user filter, and never name any other table.

scans  — one row per analysed video
    scan_id INT, filename TEXT, model TEXT, violence_detected BOOL,
    summary TEXT, labels TEXT[], created_at TIMESTAMPTZ
    model is one of: yolo, owlv2, action, llm, qwen, auto

frames — one row per object detected in a sampled video FRAME (YOLO/OWLv2)
    scan_id INT, filename TEXT, model TEXT, created_at TIMESTAMPTZ,
    second FLOAT (offset into the video), timestamp TEXT ("00:12"),
    label TEXT (e.g. Gun, Knife, Grenade), score FLOAT (0..1 confidence)

events — one row per higher-level incident segment (LLM/Qwen/action models)
    scan_id INT, filename TEXT, model TEXT, created_at TIMESTAMPTZ,
    label TEXT, category TEXT (violence|theft|harassment|accident|other),
    description TEXT, start_time FLOAT, end_time FLOAT, confidence FLOAT
