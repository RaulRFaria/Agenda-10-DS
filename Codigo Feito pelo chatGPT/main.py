"""
Sistema Escolar — protótipo didático
Python 3.10+ | Biblioteca padrão | SQLite

ATENÇÃO:
- Protótipo educacional, não adequado para dados reais sem revisão de segurança.
- A seleção de usuário no menu NÃO é autenticação real.
- O hash encadeado da auditoria ajuda a detectar alterações simples, mas não protege
  contra alguém que controle o arquivo do banco e consiga reescrever toda a cadeia.
- Para produção: autenticação robusta, banco transacional gerenciado, autorização
  no servidor, backups isolados, auditoria externa/imutável, testes e revisão jurídica.
"""

import sqlite3
import json
import hashlib
import getpass
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).with_name("escola.db")
PASSING_GRADE = 7.0

DEMO_USERS = [
    ("admin01", "Administrador", "admin", ""),
    ("coord01", "Coordenação", "coordenacao", ""),
    ("prof01", "Professor de Matemática", "professor", "MAT"),
    ("prof02", "Professor de Português", "professor", "POR"),
    ("auditor01", "Auditoria independente", "auditor", ""),
]

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con

def init_db():
    with connect() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin','coordenacao','professor','auditor')),
            subject_code TEXT
        );
        CREATE TABLE IF NOT EXISTS students (
            student_id TEXT PRIMARY KEY,
            full_name TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS subjects (
            subject_code TEXT PRIMARY KEY,
            name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS enrollments (
            student_id TEXT NOT NULL REFERENCES students(student_id),
            subject_code TEXT NOT NULL REFERENCES subjects(subject_code),
            term TEXT NOT NULL,
            PRIMARY KEY(student_id, subject_code, term)
        );
        CREATE TABLE IF NOT EXISTS rules (
            rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
            version TEXT NOT NULL UNIQUE,
            passing_grade REAL NOT NULL,
            recovery_policy TEXT NOT NULL,
            effective_term TEXT NOT NULL,
            approved_by TEXT NOT NULL,
            approved_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS grades (
            grade_id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT NOT NULL,
            subject_code TEXT NOT NULL,
            term TEXT NOT NULL,
            assessment TEXT NOT NULL,
            value REAL NOT NULL CHECK(value >= 0 AND value <= 10),
            version INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'official'
                CHECK(status IN ('official','pending_review','suspended')),
            updated_at TEXT NOT NULL,
            updated_by TEXT NOT NULL,
            FOREIGN KEY(student_id, subject_code, term)
                REFERENCES enrollments(student_id, subject_code, term),
            UNIQUE(student_id, subject_code, term, assessment)
        );
        CREATE TABLE IF NOT EXISTS grade_history (
            history_id INTEGER PRIMARY KEY AUTOINCREMENT,
            grade_id INTEGER NOT NULL REFERENCES grades(grade_id),
            old_value REAL NOT NULL,
            new_value REAL NOT NULL,
            old_version INTEGER NOT NULL,
            new_version INTEGER NOT NULL,
            reason TEXT NOT NULL,
            actor TEXT NOT NULL,
            created_at TEXT NOT NULL,
            review_status TEXT NOT NULL DEFAULT 'pending'
                CHECK(review_status IN ('pending','approved','rejected'))
        );
        CREATE TABLE IF NOT EXISTS results (
            result_id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT NOT NULL,
            subject_code TEXT NOT NULL,
            term TEXT NOT NULL,
            average REAL,
            outcome TEXT NOT NULL,
            rule_version TEXT NOT NULL,
            source_fingerprint TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('draft','pending_review','official','suspended')),
            calculated_at TEXT NOT NULL,
            calculated_by TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS documents (
            document_id TEXT PRIMARY KEY,
            student_id TEXT NOT NULL,
            term TEXT NOT NULL,
            result_id INTEGER REFERENCES results(result_id),
            version INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('draft','pending_review','valid','superseded','revoked')),
            issued_at TEXT,
            issued_by TEXT,
            replaces_document_id TEXT REFERENCES documents(document_id)
        );
        CREATE TABLE IF NOT EXISTS audit_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurred_at TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            details_json TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            event_hash TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS incidents (
            incident_id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            severity TEXT NOT NULL CHECK(severity IN ('low','medium','high','critical')),
            status TEXT NOT NULL CHECK(status IN ('open','contained','investigating','resolved')),
            description TEXT NOT NULL,
            opened_by TEXT NOT NULL,
            opened_at TEXT NOT NULL,
            closed_by TEXT,
            closed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS operations (
            operation_id TEXT PRIMARY KEY,
            payload_hash TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','processing','partial','completed','failed')),
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS operation_items (
            operation_id TEXT NOT NULL REFERENCES operations(operation_id),
            item_key TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','completed','failed')),
            detail TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY(operation_id, item_key)
        );
        """)

        con.execute("INSERT OR IGNORE INTO subjects(subject_code,name) VALUES('MAT','Matemática')")
        con.execute("INSERT OR IGNORE INTO subjects(subject_code,name) VALUES('POR','Português')")
        for user in DEMO_USERS:
            con.execute(
                "INSERT OR IGNORE INTO users(username,display_name,role,subject_code) VALUES(?,?,?,?)",
                user
            )
        con.execute("""
            INSERT OR IGNORE INTO rules(version,passing_grade,recovery_policy,effective_term,approved_by,approved_at)
            VALUES('1.0',7.0,'max(average,recovery_grade)','2026-2','admin01',?)
        """, (now(),))
        audit(con, "sistema", "database_initialized", "system", "bootstrap",
              {"note": "Initial schema and demo users created"})
        con.commit()

def audit(con, actor, action, entity_type, entity_id, details):
    last = con.execute(
        "SELECT event_hash FROM audit_events ORDER BY event_id DESC LIMIT 1"
    ).fetchone()
    prev = last["event_hash"] if last else "GENESIS"
    timestamp = now()
    payload = {
        "occurred_at": timestamp, "actor": actor, "action": action,
        "entity_type": entity_type, "entity_id": str(entity_id),
        "details": details, "previous_hash": prev
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    con.execute("""
        INSERT INTO audit_events(occurred_at,actor,action,entity_type,entity_id,details_json,previous_hash,event_hash)
        VALUES(?,?,?,?,?,?,?,?)
    """, (timestamp, actor, action, entity_type, str(entity_id),
          json.dumps(details, ensure_ascii=False, sort_keys=True), prev, digest))

def verify_audit_chain():
    with connect() as con:
        rows = con.execute("SELECT * FROM audit_events ORDER BY event_id").fetchall()
    previous = "GENESIS"
    for row in rows:
        payload = {
            "occurred_at": row["occurred_at"],
            "actor": row["actor"],
            "action": row["action"],
            "entity_type": row["entity_type"],
            "entity_id": row["entity_id"],
            "details": json.loads(row["details_json"]),
            "previous_hash": previous,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if row["previous_hash"] != previous or row["event_hash"] != digest:
            return False, row["event_id"]
        previous = row["event_hash"]
    return True, len(rows)

def current_user():
    with connect() as con:
        users = con.execute("SELECT * FROM users ORDER BY username").fetchall()
    print("\nUsuários demonstrativos (não representam login seguro):")
    for u in users:
        scope = f" — disciplina {u['subject_code']}" if u["subject_code"] else ""
        print(f"  {u['username']}: {u['display_name']} [{u['role']}]{scope}")
    username = input("Usuário da sessão demonstrativa: ").strip()
    user = next((dict(u) for u in users if u["username"] == username), None)
    if not user:
        print("Usuário não encontrado.")
        return None
    return user

def can_manage_students(user):
    return user["role"] in ("admin", "coordenacao")

def can_edit_grade(user, subject_code):
    return user["role"] in ("admin", "coordenacao") or (
        user["role"] == "professor" and user["subject_code"] == subject_code
    )

def can_approve(user):
    return user["role"] in ("admin", "coordenacao", "auditor")

def register_student(user):
    if not can_manage_students(user):
        print("Acesso negado: somente administração/coordenação cadastra alunos.")
        return
    sid = input("Matrícula/ID único: ").strip()
    name = input("Nome completo: ").strip()
    if not sid or not name:
        print("ID e nome são obrigatórios.")
        return
    with connect() as con:
        try:
            con.execute("INSERT INTO students(student_id,full_name,created_at) VALUES(?,?,?)",
                        (sid, name, now()))
            audit(con, user["username"], "student_created", "student", sid, {"full_name": name})
            con.commit()
            print("Aluno cadastrado.")
        except sqlite3.IntegrityError:
            print("Não foi possível cadastrar: ID duplicado ou dados inválidos.")

def enroll_student(user):
    if not can_manage_students(user):
        print("Acesso negado.")
        return
    sid = input("ID do aluno: ").strip()
    subject = input("Disciplina (MAT/POR): ").strip().upper()
    term = input("Período (ex.: 2026-2): ").strip()
    with connect() as con:
        try:
            con.execute("INSERT INTO enrollments(student_id,subject_code,term) VALUES(?,?,?)",
                        (sid, subject, term))
            audit(con, user["username"], "student_enrolled", "enrollment",
                  f"{sid}:{subject}:{term}", {})
            con.commit()
            print("Matrícula na disciplina registrada.")
        except sqlite3.IntegrityError:
            print("Matrícula inválida, duplicada ou aluno/disciplina inexistente.")

def add_grade(user):
    sid = input("ID do aluno: ").strip()
    subject = input("Disciplina (MAT/POR): ").strip().upper()
    term = input("Período (ex.: 2026-2): ").strip()
    assessment = input("Avaliação (ex.: prova1): ").strip()
    try:
        value = float(input("Nota (0 a 10): ").replace(",", "."))
    except ValueError:
        print("Nota inválida.")
        return
    if not 0 <= value <= 10:
        print("A nota deve estar entre 0 e 10.")
        return
    if not can_edit_grade(user, subject):
        print("Acesso negado: você não está autorizado para esta disciplina.")
        return
    with connect() as con:
        enrolled = con.execute("""
            SELECT 1 FROM enrollments WHERE student_id=? AND subject_code=? AND term=?
        """, (sid, subject, term)).fetchone()
        if not enrolled:
            print("O aluno não está matriculado nessa disciplina/período.")
            return
        try:
            cur = con.execute("""
                INSERT INTO grades(student_id,subject_code,term,assessment,value,updated_at,updated_by)
                VALUES(?,?,?,?,?,?,?)
            """, (sid, subject, term, assessment, value, now(), user["username"]))
            gid = cur.lastrowid
            audit(con, user["username"], "grade_created", "grade", gid,
                  {"student_id": sid, "subject": subject, "term": term, "assessment": assessment, "value": value})
            con.commit()
            print(f"Nota registrada. ID da nota: {gid}")
        except sqlite3.IntegrityError:
            print("Avaliação duplicada para este aluno/disciplina/período.")

def correct_grade(user):
    try:
        gid = int(input("ID da nota: "))
        expected_version = int(input("Versão atual esperada (veja a listagem): "))
        new_value = float(input("Nova nota (0 a 10): ").replace(",", "."))
    except ValueError:
        print("Entrada inválida.")
        return
    reason = input("Justificativa obrigatória: ").strip()
    if not 0 <= new_value <= 10 or not reason:
        print("Nota inválida ou justificativa ausente.")
        return
    with connect() as con:
        grade = con.execute("SELECT * FROM grades WHERE grade_id=?", (gid,)).fetchone()
        if not grade:
            print("Nota não encontrada.")
            return
        if not can_edit_grade(user, grade["subject_code"]):
            print("Acesso negado.")
            return
        if grade["version"] != expected_version:
            print("Conflito de versão: a nota mudou desde a última consulta. Releia antes de tentar novamente.")
            audit(con, user["username"], "grade_correction_conflict", "grade", gid,
                  {"expected_version": expected_version, "actual_version": grade["version"]})
            con.commit()
            return
        new_version = grade["version"] + 1
        con.execute("""
            INSERT INTO grade_history(grade_id,old_value,new_value,old_version,new_version,reason,actor,created_at)
            VALUES(?,?,?,?,?,?,?,?)
        """, (gid, grade["value"], new_value, grade["version"], new_version, reason, user["username"], now()))
        # A nota passa a exigir revisão; não é publicada silenciosamente.
        con.execute("""
            UPDATE grades SET value=?,version=?,status='pending_review',updated_at=?,updated_by=?
            WHERE grade_id=? AND version=?
        """, (new_value, new_version, now(), user["username"], gid, expected_version))
        audit(con, user["username"], "grade_corrected_pending_review", "grade", gid,
              {"old_value": grade["value"], "new_value": new_value,
               "old_version": grade["version"], "new_version": new_version, "reason": reason})
        con.commit()
        print("Correção registrada e marcada para revisão. Ela ainda não foi aprovada como oficial.")

def list_grades(user):
    with connect() as con:
        if user["role"] == "professor":
            rows = con.execute("""
                SELECT g.*, s.full_name FROM grades g JOIN students s USING(student_id)
                WHERE g.subject_code=? ORDER BY g.term,s.full_name
            """, (user["subject_code"],)).fetchall()
        else:
            rows = con.execute("""
                SELECT g.*, s.full_name FROM grades g JOIN students s USING(student_id)
                ORDER BY g.term,s.full_name,g.subject_code
            """).fetchall()
    if not rows:
        print("Nenhuma nota registrada.")
        return
    print("\nID | Aluno | Disciplina | Período | Avaliação | Nota | Versão | Estado")
    for r in rows:
        print(f"{r['grade_id']} | {r['full_name']} | {r['subject_code']} | {r['term']} | "
              f"{r['assessment']} | {r['value']:.2f} | v{r['version']} | {r['status']}")

def review_grade_corrections(user):
    if not can_approve(user):
        print("Acesso negado: requer coordenação, administração ou auditoria.")
        return
    with connect() as con:
        pending = con.execute("""
            SELECT h.*,g.student_id,g.subject_code,g.term,g.assessment
            FROM grade_history h JOIN grades g USING(grade_id)
            WHERE h.review_status='pending' ORDER BY h.history_id
        """).fetchall()
    if not pending:
        print("Não há correções pendentes.")
        return
    for h in pending:
        print(f"Histórico {h['history_id']} | nota {h['grade_id']} | aluno {h['student_id']} | "
              f"{h['old_value']} -> {h['new_value']} | autor {h['actor']} | motivo: {h['reason']}")
    try:
        hid = int(input("ID do histórico para revisar: "))
    except ValueError:
        print("ID inválido.")
        return
    choice = input("Aprovar (A) ou rejeitar (R)? ").strip().upper()
    if choice not in ("A", "R"):
        print("Opção inválida.")
        return
    with connect() as con:
        hist = con.execute("SELECT * FROM grade_history WHERE history_id=? AND review_status='pending'",
                           (hid,)).fetchone()
        if not hist:
            print("Registro não está pendente.")
            return
        status = "approved" if choice == "A" else "rejected"
        con.execute("UPDATE grade_history SET review_status=? WHERE history_id=?", (status, hid))
        grade_status = "official" if choice == "A" else "suspended"
        con.execute("UPDATE grades SET status=? WHERE grade_id=?", (grade_status, hist["grade_id"]))
        audit(con, user["username"], "grade_correction_reviewed", "grade_history", hid,
              {"decision": status, "grade_id": hist["grade_id"]})
        con.commit()
        print("Decisão registrada. Se rejeitada, a nota fica suspensa para resolução explícita; o valor não é revertido automaticamente.")

def calculate_result(user):
    sid = input("ID do aluno: ").strip()
    subject = input("Disciplina (MAT/POR): ").strip().upper()
    term = input("Período: ").strip()
    if user["role"] == "professor" and user["subject_code"] != subject:
        print("Acesso negado.")
        return
    with connect() as con:
        grades = con.execute("""
            SELECT * FROM grades WHERE student_id=? AND subject_code=? AND term=? ORDER BY assessment
        """, (sid, subject, term)).fetchall()
        if not grades:
            print("Não há notas; não será calculada média.")
            return
        if any(g["status"] != "official" for g in grades):
            print("Há notas pendentes/suspensas. O resultado ficará pendente de revisão.")
            status = "pending_review"
        else:
            status = "draft"
        rule = con.execute("""
            SELECT * FROM rules WHERE effective_term<=? ORDER BY effective_term DESC,rule_id DESC LIMIT 1
        """, (term,)).fetchone()
        if not rule:
            print("Não há regra de cálculo aplicável a este período.")
            return
        average = round(sum(g["value"] for g in grades) / len(grades), 2)
        outcome = "aprovado" if average >= rule["passing_grade"] else "recuperação"
        fingerprint_data = [(g["grade_id"], g["version"], g["value"], g["status"]) for g in grades]
        fingerprint = hashlib.sha256(json.dumps(fingerprint_data, sort_keys=True).encode()).hexdigest()
        cur = con.execute("""
            INSERT INTO results(student_id,subject_code,term,average,outcome,rule_version,source_fingerprint,
                                status,calculated_at,calculated_by)
            VALUES(?,?,?,?,?,?,?,?,?,?)
        """, (sid, subject, term, average, outcome, rule["version"], fingerprint, status, now(), user["username"]))
        rid = cur.lastrowid
        audit(con, user["username"], "result_calculated", "result", rid,
              {"student_id": sid, "subject": subject, "term": term, "average": average,
               "outcome": outcome, "rule_version": rule["version"], "status": status})
        con.commit()
        print(f"Resultado ID {rid}: média {average:.2f}, situação {outcome}, regra v{rule['version']}, estado {status}.")
        if status != "official":
            print("Nota: este protótipo não publica automaticamente resultados oficiais.")

def approve_result(user):
    if not can_approve(user):
        print("Acesso negado.")
        return
    with connect() as con:
        rows = con.execute("SELECT * FROM results WHERE status IN ('draft','pending_review') ORDER BY result_id").fetchall()
    if not rows:
        print("Nenhum resultado pendente.")
        return
    for r in rows:
        print(f"Resultado {r['result_id']} | aluno {r['student_id']} | {r['subject_code']} | "
              f"{r['term']} | média {r['average']} | estado {r['status']} | regra {r['rule_version']}")
    try:
        rid = int(input("ID do resultado para aprovar: "))
    except ValueError:
        print("ID inválido.")
        return
    with connect() as con:
        result = con.execute("SELECT * FROM results WHERE result_id=?", (rid,)).fetchone()
        if not result or result["status"] not in ("draft", "pending_review"):
            print("Resultado inexistente ou não aprovável.")
            return
        current = con.execute("""
            SELECT grade_id,version,value,status FROM grades
            WHERE student_id=? AND subject_code=? AND term=? ORDER BY assessment
        """, (result["student_id"], result["subject_code"], result["term"])).fetchall()
        current_fp = hashlib.sha256(json.dumps(
            [(g["grade_id"], g["version"], g["value"], g["status"]) for g in current],
            sort_keys=True).encode()).hexdigest()
        if current_fp != result["source_fingerprint"]:
            print("A origem mudou desde o cálculo. Recalcule antes de aprovar.")
            audit(con, user["username"], "result_approval_blocked_stale", "result", rid, {})
            con.commit()
            return
        if any(g["status"] != "official" for g in current):
            print("Há notas não oficiais. Resolva-as antes da aprovação.")
            return
        con.execute("UPDATE results SET status='official' WHERE result_id=?", (rid,))
        audit(con, user["username"], "result_approved", "result", rid,
              {"rule_version": result["rule_version"], "source_fingerprint": current_fp})
        con.commit()
        print("Resultado aprovado como oficial.")

def issue_document(user):
    if not can_approve(user):
        print("Acesso negado.")
        return
    try:
        rid = int(input("ID do resultado oficial: "))
    except ValueError:
        print("ID inválido.")
        return
    with connect() as con:
        result = con.execute("SELECT * FROM results WHERE result_id=?", (rid,)).fetchone()
        if not result or result["status"] != "official":
            print("Somente resultados oficiais podem gerar documentos.")
            return
        existing = con.execute("""
            SELECT MAX(version) AS v FROM documents WHERE student_id=? AND term=?
        """, (result["student_id"], result["term"])).fetchone()
        version = (existing["v"] or 0) + 1
        doc_id = hashlib.sha256(f"{result['student_id']}:{result['term']}:{rid}:{version}:{now()}".encode()).hexdigest()[:16]
        con.execute("""
            INSERT INTO documents(document_id,student_id,term,result_id,version,status,issued_at,issued_by)
            VALUES(?,?,?,?,?,'valid',?,?)
        """, (doc_id, result["student_id"], result["term"], rid, version, now(), user["username"]))
        audit(con, user["username"], "document_issued", "document", doc_id,
              {"result_id": rid, "version": version})
        con.commit()
        print(f"Documento emitido: {doc_id} (versão {version}).")

def list_documents(user):
    with connect() as con:
        if user["role"] == "professor":
            rows = con.execute("""
                SELECT d.* FROM documents d JOIN results r USING(result_id)
                WHERE r.subject_code=? ORDER BY d.issued_at DESC
            """, (user["subject_code"],)).fetchall()
        else:
            rows = con.execute("SELECT * FROM documents ORDER BY issued_at DESC").fetchall()
    if not rows:
        print("Nenhum documento emitido.")
    for d in rows:
        print(f"{d['document_id']} | aluno {d['student_id']} | período {d['term']} | "
              f"resultado {d['result_id']} | v{d['version']} | {d['status']} | emitido {d['issued_at']}")

def supersede_document(user):
    if not can_approve(user):
        print("Acesso negado.")
        return
    old_id = input("ID do documento anterior: ").strip()
    new_id = input("ID do documento substituto já emitido: ").strip()
    reason = input("Motivo da substituição: ").strip()
    if not reason:
        print("Motivo obrigatório.")
        return
    with connect() as con:
        old = con.execute("SELECT * FROM documents WHERE document_id=?", (old_id,)).fetchone()
        new = con.execute("SELECT * FROM documents WHERE document_id=?", (new_id,)).fetchone()
        if not old or not new or old["student_id"] != new["student_id"] or old["term"] != new["term"]:
            print("Documentos incompatíveis ou não encontrados.")
            return
        if old["status"] not in ("valid", "pending_review"):
            print("Documento anterior não pode ser substituído neste estado.")
            return
        con.execute("UPDATE documents SET status='superseded' WHERE document_id=?", (old_id,))
        con.execute("UPDATE documents SET replaces_document_id=? WHERE document_id=?", (old_id, new_id))
        audit(con, user["username"], "document_superseded", "document", old_id,
              {"replacement": new_id, "reason": reason})
        con.commit()
        print("Histórico de substituição registrado.")

def open_incident(user):
    if user["role"] not in ("admin", "coordenacao", "auditor"):
        print("Acesso negado.")
        return
    title = input("Título do incidente: ").strip()
    severity = input("Gravidade (low/medium/high/critical): ").strip().lower()
    description = input("Descrição factual, sem suposições apresentadas como certeza: ").strip()
    if not title or severity not in ("low", "medium", "high", "critical") or not description:
        print("Dados inválidos.")
        return
    with connect() as con:
        cur = con.execute("""
            INSERT INTO incidents(title,severity,status,description,opened_by,opened_at)
            VALUES(?,?,'open',?,?,?)
        """, (title, severity, description, user["username"], now()))
        iid = cur.lastrowid
        audit(con, user["username"], "incident_opened", "incident", iid,
              {"severity": severity, "title": title})
        con.commit()
        print(f"Incidente {iid} registrado.")

def update_incident(user):
    if user["role"] not in ("admin", "coordenacao", "auditor"):
        print("Acesso negado.")
        return
    try:
        iid = int(input("ID do incidente: "))
    except ValueError:
        print("ID inválido.")
        return
    status = input("Novo estado (open/contained/investigating/resolved): ").strip().lower()
    if status not in ("open", "contained", "investigating", "resolved"):
        print("Estado inválido.")
        return
    with connect() as con:
        incident = con.execute("SELECT * FROM incidents WHERE incident_id=?", (iid,)).fetchone()
        if not incident:
            print("Incidente não encontrado.")
            return
        con.execute("""
            UPDATE incidents SET status=?,closed_by=?,closed_at=? WHERE incident_id=?
        """, (status, user["username"] if status == "resolved" else None,
              now() if status == "resolved" else None, iid))
        audit(con, user["username"], "incident_status_changed", "incident", iid,
              {"old_status": incident["status"], "new_status": status})
        con.commit()
        print("Estado atualizado.")

def list_incidents(user):
    if user["role"] == "professor":
        print("Acesso negado.")
        return
    with connect() as con:
        rows = con.execute("SELECT * FROM incidents ORDER BY incident_id DESC").fetchall()
    for r in rows:
        print(f"{r['incident_id']} | {r['severity']} | {r['status']} | {r['title']} | "
              f"aberto por {r['opened_by']} em {r['opened_at']}")

def create_bulk_operation(user):
    if user["role"] not in ("admin", "coordenacao"):
        print("Acesso negado.")
        return
    op_id = input("ID único da operação (ex.: CORRECAO-2026-0001): ").strip()
    payload = input("Descrição/payload resumido da operação: ").strip()
    if not op_id or not payload:
        print("ID e descrição são obrigatórios.")
        return
    payload_hash = hashlib.sha256(payload.encode()).hexdigest()
    items_text = input("Itens separados por vírgula (ex.: aluno01-MAT,aluno02-POR): ").strip()
    items = [x.strip() for x in items_text.split(",") if x.strip()]
    with connect() as con:
        existing = con.execute("SELECT * FROM operations WHERE operation_id=?", (op_id,)).fetchone()
        if existing:
            if existing["payload_hash"] != payload_hash:
                print("ERRO: o mesmo ID foi reutilizado com payload diferente. Operação bloqueada.")
                audit(con, user["username"], "operation_id_payload_conflict", "operation", op_id, {})
                con.commit()
                return
            print(f"Operação já existe com estado {existing['status']}; não será duplicada.")
            return
        con.execute("""
            INSERT INTO operations(operation_id,payload_hash,status,created_by,created_at,updated_at,summary)
            VALUES(?,?,'pending',?,?,?,?)
        """, (op_id, payload_hash, user["username"], now(), now(), payload))
        for item in items:
            con.execute("""
                INSERT INTO operation_items(operation_id,item_key,status,updated_at)
                VALUES(?,?,'pending',?)
            """, (op_id, item, now()))
        audit(con, user["username"], "bulk_operation_created", "operation", op_id,
              {"item_count": len(items), "payload_hash": payload_hash})
        con.commit()
        print(f"Operação {op_id} criada com {len(items)} itens pendentes.")

def process_bulk_operation(user):
    if user["role"] not in ("admin", "coordenacao"):
        print("Acesso negado.")
        return
    op_id = input("ID da operação: ").strip()
    with connect() as con:
        op = con.execute("SELECT * FROM operations WHERE operation_id=?", (op_id,)).fetchone()
        if not op:
            print("Operação não encontrada.")
            return
        items = con.execute("SELECT * FROM operation_items WHERE operation_id=? ORDER BY item_key",
                            (op_id,)).fetchall()
        if not items:
            print("Operação sem itens.")
            return
        con.execute("UPDATE operations SET status='processing',updated_at=? WHERE operation_id=?", (now(), op_id))
        # Demonstração: cada item é marcado individualmente. Em produção, cada efeito externo
        # precisaria de confirmação/idempotência no sistema destinatário.
        for item in items:
            if item["status"] == "completed":
                continue
            choice = input(f"Item {item['item_key']} — marcar como concluído (C) ou falhou (F)? ").strip().upper()
            if choice == "C":
                status, detail = "completed", "Confirmação registrada pelo operador"
            elif choice == "F":
                status, detail = "failed", "Falha registrada pelo operador"
            else:
                status, detail = "pending", "Sem confirmação; permanece pendente"
            con.execute("""
                UPDATE operation_items SET status=?,detail=?,updated_at=?
                WHERE operation_id=? AND item_key=?
            """, (status, detail, now(), op_id, item["item_key"]))
            audit(con, user["username"], "bulk_operation_item_updated", "operation_item",
                  f"{op_id}:{item['item_key']}", {"status": status, "detail": detail})
        counts = con.execute("""
            SELECT status,COUNT(*) AS n FROM operation_items WHERE operation_id=? GROUP BY status
        """, (op_id,)).fetchall()
        count_map = {x["status"]: x["n"] for x in counts}
        if count_map.get("pending", 0) > 0:
            status = "partial"
        elif count_map.get("failed", 0) > 0:
            status = "failed"
        else:
            status = "completed"
        summary = json.dumps(count_map, ensure_ascii=False, sort_keys=True)
        con.execute("UPDATE operations SET status=?,updated_at=?,summary=? WHERE operation_id=?",
                    (status, now(), summary, op_id))
        audit(con, user["username"], "bulk_operation_reconciled", "operation", op_id,
              {"status": status, "counts": count_map})
        con.commit()
        print(f"Reconciliação registrada: {status} — {summary}")

def list_operations(user):
    if user["role"] not in ("admin", "coordenacao", "auditor"):
        print("Acesso negado.")
        return
    with connect() as con:
        ops = con.execute("SELECT * FROM operations ORDER BY created_at DESC").fetchall()
        for op in ops:
            print(f"\n{op['operation_id']} | {op['status']} | {op['summary']}")
            items = con.execute("SELECT * FROM operation_items WHERE operation_id=? ORDER BY item_key",
                                (op["operation_id"],)).fetchall()
            for item in items:
                print(f"  - {item['item_key']}: {item['status']} — {item['detail']}")

def show_audit(user):
    if user["role"] not in ("admin", "coordenacao", "auditor"):
        print("Acesso negado.")
        return
    with connect() as con:
        rows = con.execute("SELECT * FROM audit_events ORDER BY event_id DESC LIMIT 50").fetchall()
    for r in rows:
        print(f"{r['event_id']} | {r['occurred_at']} | {r['actor']} | {r['action']} | "
              f"{r['entity_type']}:{r['entity_id']} | {r['details_json']}")

def verify_audit(user):
    if user["role"] not in ("admin", "auditor"):
        print("Acesso negado.")
        return
    ok, info = verify_audit_chain()
    if ok:
        print(f"Cadeia de auditoria consistente nos {info} eventos verificados.")
    else:
        print(f"ALERTA: possível alteração ou quebra da cadeia no evento {info}.")
    with connect() as con:
        audit(con, user["username"], "audit_chain_verified", "audit", "chain",
              {"valid": ok, "info": info})
        con.commit()

def list_students(user):
    if user["role"] == "professor":
        with connect() as con:
            rows = con.execute("""
                SELECT DISTINCT s.* FROM students s
                JOIN enrollments e USING(student_id)
                WHERE e.subject_code=? ORDER BY s.full_name
            """, (user["subject_code"],)).fetchall()
    else:
        with connect() as con:
            rows = con.execute("SELECT * FROM students ORDER BY full_name").fetchall()
    for r in rows:
        print(f"{r['student_id']} | {r['full_name']} | {'ativo' if r['active'] else 'inativo'}")

def menu():
    init_db()
    print("=" * 58)
    print("SISTEMA ESCOLAR — PROTÓTIPO DIDÁTICO")
    print("=" * 58)
    print("O banco escola.db será criado nesta pasta na primeira execução.")
    while True:
        user = current_user()
        if not user:
            if input("Tentar novamente? (s/n): ").strip().lower() != "s":
                break
            continue
        while True:
            print(f"\nSessão demonstrativa: {user['display_name']} [{user['role']}]")
            print("""
1. Cadastrar aluno
2. Matricular aluno em disciplina/período
3. Registrar nota
4. Listar notas
5. Corrigir nota (gera revisão pendente)
6. Revisar correção de nota
7. Calcular resultado
8. Aprovar resultado
9. Emitir documento
10. Listar documentos
11. Marcar documento como substituído
12. Abrir incidente
13. Atualizar estado de incidente
14. Listar incidentes
15. Criar operação em lote
16. Processar/reconciliar operação em lote
17. Listar operações em lote
18. Consultar auditoria
19. Verificar cadeia de auditoria
20. Listar alunos
0. Encerrar sessão
q. Sair do sistema
""")
            choice = input("Escolha: ").strip().lower()
            actions = {
                "1": register_student, "2": enroll_student, "3": add_grade,
                "4": list_grades, "5": correct_grade, "6": review_grade_corrections,
                "7": calculate_result, "8": approve_result, "9": issue_document,
                "10": list_documents, "11": supersede_document, "12": open_incident,
                "13": update_incident, "14": list_incidents, "15": create_bulk_operation,
                "16": process_bulk_operation, "17": list_operations, "18": show_audit,
                "19": verify_audit, "20": list_students
            }
            if choice == "0":
                break
            if choice == "q":
                return
            action = actions.get(choice)
            if action:
                try:
                    action(user)
                except sqlite3.Error as exc:
                    print(f"Erro de banco de dados: {exc}")
                except KeyboardInterrupt:
                    print("\nOperação interrompida. Verifique o estado antes de repetir.")
            else:
                print("Opção inválida.")

if __name__ == "__main__":
    menu()
