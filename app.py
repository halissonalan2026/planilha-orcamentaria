import base64
from datetime import date, timedelta
from functools import wraps
import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
from flask import (
    Flask,
    redirect,
    render_template_string,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "construtora-quality-chave-2026")

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "admin123")

# Persistência no Render: usa /var/data quando disponível, com fallback local
DATA_DIR = os.environ.get("DATA_DIR", "/var/data")
try:
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.access(DATA_DIR, os.W_OK):
        DATA_DIR = "."
except OSError:
    DATA_DIR = "."

DB_PATH = os.environ.get("DB_PATH", os.path.join(DATA_DIR, "orcamento_obras.db"))
UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", os.path.join(DATA_DIR, "uploads", "pdfs"))
LOGOS_FOLDER = os.environ.get("LOGOS_FOLDER", os.path.join(DATA_DIR, "uploads", "logos"))

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["LOGOS_FOLDER"] = LOGOS_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(LOGOS_FOLDER, exist_ok=True)

EXTENSOES_LOGO = {".png", ".jpg", ".jpeg", ".webp"}

ETAPAS_PADRAO = [
    "Serviços Preliminares e Gerais",
    "Infraestrutura",
    "Superestrutura",
    "Paredes e Painéis",
    "Esquadrias",
    "Vidros e Plásticos",
    "Coberturas",
    "Impermeabilizações",
    "Revestimentos Internos",
    "Forros",
    "Revestimentos Externos",
    "Pintura",
    "Pisos",
    "Acabamentos",
    "Inst. Elétricas e Telefônicas",
    "Instalações Hidráulicas",
    "Inst. Esgoto e Águas Pluviais",
    "Louças e Metais",
    "Outros Serviços (Muro, portão, calçada)",
]

CATEGORIAS_OBRA = ["Alvenaria", "Hidráulica", "Elétrica", "Pintura"]


def format_brl(valor):
    """Formata número no padrão brasileiro: R$ 1.234,56"""
    try:
        num = float(valor or 0.0)
    except (ValueError, TypeError):
        num = 0.0
    sinal = "-" if num < 0 else ""
    abs_val = abs(num)
    parte_inteira, parte_decimal = f"{abs_val:.2f}".split(".")
    inteira_formatada = ""
    for i, digito in enumerate(reversed(parte_inteira)):
        if i > 0 and i % 3 == 0:
            inteira_formatada = "." + inteira_formatada
        inteira_formatada = digito + inteira_formatada
    return f"{sinal}R$ {inteira_formatada},{parte_decimal}"


def format_num_br(valor, decimais=2):
    """Formata número decimal no padrão brasileiro sem R$: 1.234,56"""
    try:
        num = float(valor or 0.0)
    except (ValueError, TypeError):
        num = 0.0
    sinal = "-" if num < 0 else ""
    abs_val = abs(num)
    parte_inteira, parte_decimal = f"{abs_val:.{decimais}f}".split(".")
    inteira_formatada = ""
    for i, digito in enumerate(reversed(parte_inteira)):
        if i > 0 and i % 3 == 0:
            inteira_formatada = "." + inteira_formatada
        inteira_formatada = digito + inteira_formatada
    if decimais == 0:
        return f"{sinal}{inteira_formatada}"
    return f"{sinal}{inteira_formatada},{parte_decimal}"


def parse_moeda(texto):
    """Converte entrada com vírgula ou ponto (ex.: '1.500,50' ou '1500.50') para float."""
    if texto is None:
        return 0.0
    limpo = str(texto).strip().replace("R$", "").replace("r$", "").replace(" ", "")
    if not limpo:
        return 0.0
    if "," in limpo and "." in limpo:
        limpo = limpo.replace(".", "").replace(",", ".")
    elif "," in limpo:
        limpo = limpo.replace(",", ".")
    try:
        return float(limpo)
    except ValueError:
        return 0.0


def _gerar_token_redefinicao(user_id: int, senha_hash: str) -> str:
    payload = {
        "uid": int(user_id),
        "exp": int(time.time()) + 1800,
        "p": (senha_hash or "")[:16],
    }
    raw_json = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(raw_json).decode("ascii").rstrip("=")
    sig = hmac.new(
        app.secret_key.encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload_b64}.{sig}"


def _validar_token_redefinicao(token: str):
    try:
        payload_b64, sig = token.split(".", 1)
        expected_sig = hmac.new(
            app.secret_key.encode("utf-8"),
            payload_b64.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None, "invalid"
        padding = "=" * ((4 - len(payload_b64) % 4) % 4)
        raw_json = base64.urlsafe_b64decode((payload_b64 + padding).encode("ascii"))
        dados = json.loads(raw_json.decode("utf-8"))
        if int(dados.get("exp", 0)) < int(time.time()):
            return None, "expired"
        return dados, "ok"
    except Exception:
        return None, "invalid"


def get_db_connection():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _coluna_existe(cursor, tabela, coluna):
    cursor.execute(f"PRAGMA table_info({tabela})")
    return any(row["name"] == coluna for row in cursor.fetchall())


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            whatsapp TEXT NOT NULL,
            usuario TEXT NOT NULL UNIQUE,
            senha_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pendente',
            vencimento TEXT,
            nome_empresa TEXT,
            logo_arquivo TEXT,
            criado_em TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """
    )
    if not _coluna_existe(cursor, "usuarios", "nome_empresa"):
        cursor.execute("ALTER TABLE usuarios ADD COLUMN nome_empresa TEXT")
    if not _coluna_existe(cursor, "usuarios", "logo_arquivo"):
        cursor.execute("ALTER TABLE usuarios ADD COLUMN logo_arquivo TEXT")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            nome TEXT NOT NULL,
            telefone TEXT,
            endereco TEXT,
            FOREIGN KEY (usuario_id) REFERENCES usuarios (id) ON DELETE CASCADE
        )
    """
    )
    if not _coluna_existe(cursor, "clientes", "usuario_id"):
        cursor.execute("ALTER TABLE clientes ADD COLUMN usuario_id INTEGER")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS fornecedores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            nome TEXT NOT NULL,
            telefone TEXT,
            material_principal TEXT,
            tipo_fornecedor TEXT NOT NULL DEFAULT 'Material',
            FOREIGN KEY (usuario_id) REFERENCES usuarios (id) ON DELETE CASCADE
        )
    """
    )
    if not _coluna_existe(cursor, "fornecedores", "usuario_id"):
        cursor.execute("ALTER TABLE fornecedores ADD COLUMN usuario_id INTEGER")
    if not _coluna_existe(cursor, "fornecedores", "tipo_fornecedor"):
        cursor.execute(
            "ALTER TABLE fornecedores ADD COLUMN tipo_fornecedor TEXT NOT NULL DEFAULT 'Material'"
        )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS estoque (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            descricao TEXT NOT NULL,
            fornecedor_id INTEGER,
            unidade TEXT NOT NULL DEFAULT 'un',
            qtd_comprada REAL NOT NULL,
            valor_total_lote REAL NOT NULL,
            valor_unitario REAL GENERATED ALWAYS AS (valor_total_lote / NULLIF(qtd_comprada, 0)) STORED,
            FOREIGN KEY (usuario_id) REFERENCES usuarios (id) ON DELETE CASCADE,
            FOREIGN KEY (fornecedor_id) REFERENCES fornecedores (id) ON DELETE SET NULL
        )
    """
    )
    if not _coluna_existe(cursor, "estoque", "usuario_id"):
        cursor.execute("ALTER TABLE estoque ADD COLUMN usuario_id INTEGER")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS obras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            cliente_id INTEGER,
            nome_obra TEXT NOT NULL,
            valor_financiamento REAL DEFAULT 0,
            valor_entrada REAL DEFAULT 0,
            valor_terreno REAL DEFAULT 0,
            metro_quadrado REAL DEFAULT 0,
            valor_m2_usuario REAL DEFAULT 0,
            pdf_projeto TEXT,
            FOREIGN KEY (usuario_id) REFERENCES usuarios (id) ON DELETE CASCADE,
            FOREIGN KEY (cliente_id) REFERENCES clientes (id) ON DELETE SET NULL
        )
    """
    )
    if not _coluna_existe(cursor, "obras", "usuario_id"):
        cursor.execute("ALTER TABLE obras ADD COLUMN usuario_id INTEGER")
    if not _coluna_existe(cursor, "obras", "valor_m2_usuario"):
        cursor.execute("ALTER TABLE obras ADD COLUMN valor_m2_usuario REAL DEFAULT 0")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS etapas_obra (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            obra_id INTEGER,
            nome_etapa TEXT NOT NULL,
            percentual REAL DEFAULT 0,
            FOREIGN KEY (obra_id) REFERENCES obras (id) ON DELETE CASCADE
        )
    """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS materiais_obra (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            obra_id INTEGER,
            fornecedor_id INTEGER,
            estoque_id INTEGER,
            categoria TEXT NOT NULL DEFAULT 'Alvenaria',
            tipo_item TEXT NOT NULL DEFAULT 'Material',
            descricao TEXT NOT NULL,
            unidade TEXT NOT NULL DEFAULT 'un',
            quantidade REAL NOT NULL,
            valor_unitario REAL NOT NULL,
            valor_total REAL GENERATED ALWAYS AS (quantidade * valor_unitario) STORED,
            FOREIGN KEY (obra_id) REFERENCES obras (id) ON DELETE CASCADE,
            FOREIGN KEY (fornecedor_id) REFERENCES fornecedores (id) ON DELETE SET NULL,
            FOREIGN KEY (estoque_id) REFERENCES estoque (id) ON DELETE SET NULL
        )
    """
    )
    if not _coluna_existe(cursor, "materiais_obra", "categoria"):
        cursor.execute(
            "ALTER TABLE materiais_obra ADD COLUMN categoria TEXT NOT NULL DEFAULT 'Alvenaria'"
        )
    if not _coluna_existe(cursor, "materiais_obra", "tipo_item"):
        cursor.execute(
            "ALTER TABLE materiais_obra ADD COLUMN tipo_item TEXT NOT NULL DEFAULT 'Material'"
        )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS pagamentos_mo_obra (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            obra_id INTEGER NOT NULL,
            fornecedor_id INTEGER,
            categoria TEXT NOT NULL DEFAULT 'Alvenaria',
            profissional_nome TEXT NOT NULL,
            valor_total_contrato REAL NOT NULL DEFAULT 0,
            valor_parcela REAL NOT NULL DEFAULT 0,
            observacao TEXT,
            criado_em TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (obra_id) REFERENCES obras (id) ON DELETE CASCADE,
            FOREIGN KEY (fornecedor_id) REFERENCES fornecedores (id) ON DELETE SET NULL
        )
    """
    )

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_clientes_usuario ON clientes (usuario_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fornecedores_usuario ON fornecedores (usuario_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_estoque_usuario ON estoque (usuario_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_obras_usuario ON obras (usuario_id)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_pag_mo_obra_cat ON pagamentos_mo_obra (obra_id, categoria)"
    )

    conn.commit()
    conn.close()


init_db()


def normalizar_whatsapp(fone):
    digitos = re.sub(r"\D", "", fone or "")
    if not digitos:
        return ""
    if not digitos.startswith("55") and len(digitos) in (10, 11):
        digitos = "55" + digitos
    return digitos


def montar_mensagem_cobranca(nome, vencimento):
    venc_txt = vencimento if vencimento else "recente"
    return (
        f"Olá {nome}, tudo bem? Passando aqui de forma amigável para lembrar sobre a "
        f"mensalidade do sistema (vencimento: {venc_txt}). Qualquer dúvida ou se já "
        f"tiver realizado o pagamento, é só me avisar por aqui para mantermos seu acesso liberado!"
    )


def current_usuario_id():
    return session.get("usuario_id")


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("is_admin"):
            return redirect(url_for("admin_painel"))
        if not session.get("usuario_id"):
            return redirect(url_for("login", next=request.path))
        if session.get("status_usuario") != "ativo":
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)

    return decorated


BASE_CSS = """
    * { box-sizing: border-box; }
    body {
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        margin: 0;
        background-color: #0f1115;
        color: #e2e8f0;
        display: flex;
        min-height: 100vh;
    }
    .watermark-had {
        position: fixed;
        right: 18px;
        bottom: 14px;
        font-family: 'Montserrat', 'Inter', 'Segoe UI', system-ui, sans-serif;
        font-size: 13px;
        font-weight: 500;
        letter-spacing: 0.28em;
        color: #e2e8f0;
        opacity: 0.14;
        pointer-events: none;
        user-select: none;
        z-index: 9999;
    }
    .logo-placeholder-had {
        width: 42px;
        height: 42px;
        border-radius: 6px;
        background: #1a1e29;
        border: 1px dashed #4a5568;
        color: #a0aec0;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 16px;
        font-weight: 700;
        flex-shrink: 0;
    }
    .sidebar {
        width: 260px;
        background: #161920;
        color: #e2e8f0;
        display: flex;
        flex-direction: column;
        border-right: 1px solid #2d3748;
        position: fixed;
        height: 100vh;
        left: 0; top: 0;
        box-shadow: 4px 0 15px rgba(0,0,0,0.3);
        z-index: 100;
        transition: all 0.3s ease;
    }
    .sidebar-header {
        padding: 20px;
        display: flex;
        align-items: center;
        gap: 12px;
        background: #111318;
        border-bottom: 2px solid #d4af37;
    }
    .sidebar-header img {
        width: 42px;
        height: 42px;
        border-radius: 6px;
        object-fit: cover;
        border: 1px solid #d4af37;
    }
    .sidebar-header h2 {
        font-size: 14px;
        margin: 0;
        color: #d4af37;
        line-height: 1.2;
        letter-spacing: 0.5px;
    }
    .sidebar-menu {
        list-style: none;
        padding: 15px 0;
        margin: 0;
        flex: 1;
    }
    .sidebar-menu li a {
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 12px 25px;
        color: #a0aec0;
        text-decoration: none;
        font-size: 14px;
        font-weight: 500;
        transition: all 0.2s;
        border-left: 4px solid transparent;
    }
    .sidebar-menu li a:hover, .sidebar-menu li a.active {
        color: #d4af37;
        background: rgba(212, 175, 55, 0.1);
        border-left-color: #d4af37;
        font-weight: 600;
    }
    .sidebar-footer {
        padding: 15px 25px;
        border-top: 1px solid #2d3748;
    }
    .sidebar-footer a {
        color: #fc8181;
        text-decoration: none;
        font-size: 14px;
        font-weight: 600;
        display: flex;
        align-items: center;
        gap: 10px;
    }
    .sidebar-footer a:hover {
        color: #feb2b2;
    }
    .main-wrapper {
        margin-left: 260px;
        flex: 1;
        display: flex;
        flex-direction: column;
        width: calc(100% - 260px);
    }
    .top-navbar {
        height: 70px;
        background: #161920;
        border-bottom: 1px solid #2d3748;
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 0 35px;
        color: #fff;
    }
    .top-navbar .user-info {
        display: flex;
        align-items: center;
        gap: 15px;
        font-size: 14px;
        color: #d4af37;
    }
    .content-area {
        padding: 30px 35px;
        flex: 1;
        overflow-x: auto;
    }
    .top-bar-title {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 25px;
        flex-wrap: wrap;
        gap: 15px;
    }
    .top-bar-title h1 {
        margin: 0;
        font-size: 22px;
        color: #ffffff;
    }
    .top-bar-title p {
        margin: 5px 0 0 0;
        color: #a0aec0;
        font-size: 13px;
    }
    .metrics-container {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 20px;
        margin-bottom: 25px;
    }
    .metric-card {
        background: #1a1e29;
        padding: 20px 25px;
        border-radius: 8px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.2);
        border: 1px solid #2d3748;
        border-top: 4px solid #d4af37;
    }
    .metric-card h4 {
        margin: 0 0 8px 0;
        color: #a0aec0;
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    .metric-card .value {
        font-size: 24px;
        font-weight: bold;
        color: #ffffff;
    }
    .card {
        background: #1a1e29;
        padding: 25px;
        border-radius: 8px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.2);
        border: 1px solid #2d3748;
        margin-bottom: 25px;
        overflow-x: auto;
    }
    .card h2, .card h3 {
        color: #ffffff;
    }
    .card-header-toggle {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        cursor: pointer;
        user-select: none;
    }
    .card-header-toggle:hover h3,
    .card-header-toggle:hover h2 {
        color: #d4af37;
    }
    .toggle-arrow {
        background: #111318;
        color: #d4af37;
        border: 1px solid #2d3748;
        border-radius: 6px;
        padding: 4px 10px;
        font-size: 13px;
        font-weight: bold;
        transition: transform 0.2s, background 0.2s;
        flex-shrink: 0;
    }
    .collapsible-body.collapsed {
        display: none;
    }
    table {
        width: 100%;
        border-collapse: collapse;
        margin-top: 15px;
        background: #161920;
        border-radius: 6px;
        overflow: hidden;
        min-width: 600px;
    }
    th, td {
        padding: 14px 16px;
        border-bottom: 1px solid #2d3748;
        text-align: left;
        font-size: 13px;
        color: #cbd5e0;
    }
    th {
        background-color: #111318;
        color: #d4af37;
        font-weight: 600;
        text-transform: uppercase;
        font-size: 11px;
        letter-spacing: 0.8px;
    }
    tr:hover {
        background-color: #202534;
    }
    .btn {
        background: #d4af37;
        color: #111318;
        padding: 12px 20px;
        text-decoration: none;
        border-radius: 6px;
        display: inline-block;
        font-weight: 600;
        border: none;
        cursor: pointer;
        font-size: 14px;
        transition: all 0.2s;
        text-align: center;
    }
    .btn:hover {
        background: #e6be40;
    }
    .btn-secundario {
        background: #2d3748;
        color: #d4af37;
    }
    .btn-secundario:hover {
        background: #4a5568;
    }
    .btn-detalhe {
        background: #2d3748;
        color: #e2e8f0;
        padding: 6px 12px;
        border-radius: 4px;
        font-size: 12px;
        text-decoration: none;
        font-weight: 600;
        border: none;
        cursor: pointer;
        display: inline-block;
        margin-right: 4px;
        margin-bottom: 4px;
    }
    .btn-detalhe:hover {
        background: #4a5568;
    }
    .btn-excluir {
        background: rgba(231, 76, 60, 0.2);
        color: #fc8181;
        padding: 6px 12px;
        border-radius: 4px;
        font-size: 12px;
        text-decoration: none;
        font-weight: 600;
        border: none;
        cursor: pointer;
        display: inline-block;
        margin-bottom: 4px;
    }
    .btn-excluir:hover {
        background: rgba(231, 76, 60, 0.3);
    }
    input, select {
        padding: 12px;
        border: 1px solid #2d3748;
        border-radius: 6px;
        font-size: 14px;
        outline: none;
        background: #111318;
        color: #ffffff;
        width: 100%;
        margin-top: 5px;
        margin-bottom: 12px;
    }
    input:focus, select:focus {
        border-color: #d4af37;
        box-shadow: 0 0 0 2px rgba(212, 175, 55, 0.2);
    }
    a.voltar {
        display: inline-block;
        margin-bottom: 20px;
        color: #d4af37;
        font-weight: 600;
        text-decoration: none;
        font-size: 14px;
    }
    a.voltar:hover {
        text-decoration: underline;
    }
    hr {
        border: 0;
        border-top: 1px solid #2d3748;
        margin: 15px 0;
    }
    .badge-tipo {
        padding: 3px 8px;
        border-radius: 4px;
        font-size: 11px;
        font-weight: 600;
    }
    .badge-mat {
        background: rgba(99, 179, 237, 0.18);
        color: #63b3ed;
    }
    .badge-mo {
        background: rgba(212, 175, 55, 0.2);
        color: #d4af37;
    }
    .login-container {
        min-height: 100vh;
        width: 100%;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 20px;
        background: #0f1115;
    }
    .login-box {
        width: 100%;
        max-width: 400px;
        background: #1a1e29;
        border: 1px solid #2d3748;
        border-top: 4px solid #d4af37;
        border-radius: 8px;
        padding: 30px 25px;
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
    }

    @media (max-width: 768px) {
        .watermark-had {
            font-size: 11px;
            right: 12px;
            bottom: 10px;
            letter-spacing: 0.22em;
        }
        .sidebar {
            width: 100%;
            height: auto;
            position: relative;
            box-shadow: none;
        }
        .sidebar-header {
            padding: 12px 15px;
            justify-content: space-between;
        }
        .sidebar-header img, .logo-placeholder-had {
            width: 32px;
            height: 32px;
            font-size: 14px;
        }
        .sidebar-header h2 {
            font-size: 12px;
        }
        .mobile-logout-link {
            display: inline-block !important;
            color: #fc8181;
            font-size: 12px;
            font-weight: 600;
            text-decoration: none;
        }
        .sidebar-menu {
            display: flex;
            flex-wrap: wrap;
            justify-content: space-around;
            padding: 8px 5px;
            background: #111318;
        }
        .sidebar-menu li a {
            padding: 8px 10px;
            font-size: 11px;
            border-left: none;
            border-bottom: 3px solid transparent;
            color: #fff;
        }
        .sidebar-menu li a.active, .sidebar-menu li a:hover {
            border-left-color: transparent;
            border-bottom-color: #d4af37;
            color: #d4af37;
        }
        .sidebar-footer {
            display: none;
        }
        .main-wrapper {
            margin-left: 0;
            width: 100%;
        }
        .top-navbar {
            display: none;
        }
        .content-area {
            padding: 15px 12px;
        }
        .metrics-container {
            grid-template-columns: 1fr;
            gap: 12px;
        }
        .card {
            padding: 18px 15px;
        }
        h1 {
            font-size: 20px !important;
        }
    }
    .mobile-logout-link {
        display: none;
    }
"""

TOGGLE_SCRIPT = """
<script>
function toggleSection(headerEl, targetId) {
    const bodyEl = document.getElementById(targetId);
    const arrowEl = headerEl.querySelector('.toggle-arrow');
    if (!bodyEl) return;
    const isCollapsed = bodyEl.classList.toggle('collapsed');
    if (arrowEl) {
        arrowEl.textContent = isCollapsed ? '▸ Abrir' : '▾ Fechar';
    }
}

document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.collapsible-body').forEach(function (el) {
        el.classList.add('collapsed');
        const header = el.previousElementSibling;
        if (header && header.classList.contains('card-header-toggle')) {
            const arrow = header.querySelector('.toggle-arrow');
            if (arrow) arrow.textContent = '▸ Abrir';
        }
    });
});
</script>
"""


def obter_branding_usuario(conn=None):
    nome_empresa = ""
    logo_url = ""

    uid = current_usuario_id()
    if uid:
        fechar_conn = False
        if conn is None:
            conn = get_db_connection()
            fechar_conn = True
        row = conn.execute(
            "SELECT nome_empresa, logo_arquivo FROM usuarios WHERE id = ?", (uid,)
        ).fetchone()
        if fechar_conn:
            conn.close()

        if row:
            if (row["nome_empresa"] or "").strip():
                nome_empresa = row["nome_empresa"].strip()
            if (row["logo_arquivo"] or "").strip():
                logo_url = url_for(
                    "uploaded_logo", filename=os.path.basename(row["logo_arquivo"])
                )

    return nome_empresa, logo_url


def render_layout(body_content, active_menu="resumo", nome_empresa=None, logo_url=None):
    menu_active = {
        "resumo": "",
        "clientes": "",
        "obra": "",
        "fornecedores": "",
        "estoque": "",
        "empresa": "",
    }
    if active_menu in menu_active:
        menu_active[active_menu] = "active"

    if nome_empresa is None or logo_url is None:
        empresa_padrao, logo_padrao = obter_branding_usuario()
        nome_empresa = empresa_padrao if nome_empresa is None else nome_empresa
        logo_url = logo_padrao if logo_url is None else logo_url

    titulo_exibicao = nome_empresa.strip() if (nome_empresa or "").strip() else "?"
    logo_html = (
        f'<img src="{logo_url}" alt="Logo">'
        if (logo_url or "").strip()
        else '<div class="logo-placeholder-had">?</div>'
    )
    nome_usuario = session.get("nome_usuario") or "Profissional"

    return render_template_string(
        f"""
        <!DOCTYPE html>
        <html lang="pt-br">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{titulo_exibicao} - Gestão de Obras</title>
            <style>{BASE_CSS}</style>
        </head>
        <body>
            <div class="sidebar">
                <div class="sidebar-header">
                    <div style="display:flex; align-items:center; gap:12px;">
                        {logo_html}
                        <h2>{titulo_exibicao}</h2>
                    </div>
                    <a href="/logout" class="mobile-logout-link">Sair</a>
                </div>
                <ul class="sidebar-menu">
                    <li><a href="/" class="{menu_active['resumo']}">📊 <span>Resumo</span></a></li>
                    <li><a href="/novo_cliente" class="{menu_active['clientes']}">👤 <span>Clientes</span></a></li>
                    <li><a href="/nova_obra" class="{menu_active['obra']}">🏗️ <span>Registar</span></a></li>
                    <li><a href="/fornecedores" class="{menu_active['fornecedores']}">🤝 <span>Fornecedores</span></a></li>
                    <li><a href="/estoque" class="{menu_active['estoque']}">📦 <span>Estoque</span></a></li>
                    <li><a href="/minha_empresa" class="{menu_active['empresa']}">🏢 <span>Minha Empresa</span></a></li>
                </ul>
                <div class="sidebar-footer">
                    <a href="/logout">🚪 Sair do Sistema</a>
                </div>
            </div>

            <div class="main-wrapper">
                <div class="top-navbar">
                    <span style="font-weight: 600; font-size: 13px; letter-spacing: 0.5px; color: #d4af37;">GESTÃO DE OBRAS</span>
                    <div class="user-info">
                        <span style="font-size: 13px;">{nome_usuario} ▾</span>
                    </div>
                </div>
                <div class="content-area">
                    {body_content}
                </div>
            </div>
            <div class="watermark-had">HAD</div>
            {TOGGLE_SCRIPT}
        </body>
        </html>
    """
    )


@app.route("/cadastro", methods=["GET", "POST"])
def cadastro():
    mensagem = ""
    erro = ""

    if request.method == "POST":
        nome = (request.form.get("nome") or "").strip()
        whatsapp = (request.form.get("whatsapp") or "").strip()
        usuario = (request.form.get("usuario") or "").strip().lower()
        senha = request.form.get("senha") or ""

        if not nome or not whatsapp or not usuario or not senha:
            erro = "Preencha nome, WhatsApp, usuário e senha."
        elif usuario == ADMIN_USER.lower():
            erro = "Esse nome de usuário não está disponível."
        else:
            conn = get_db_connection()
            existente = conn.execute(
                "SELECT id FROM usuarios WHERE usuario = ?", (usuario,)
            ).fetchone()
            if existente:
                conn.close()
                erro = "Este usuário já está cadastrado."
            else:
                venc_padrao = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
                conn.execute(
                    """
                    INSERT INTO usuarios (nome, whatsapp, usuario, senha_hash, status, vencimento)
                    VALUES (?, ?, ?, ?, 'pendente', ?)
                """,
                    (nome, whatsapp, usuario, generate_password_hash(senha), venc_padrao),
                )
                conn.commit()
                conn.close()
                mensagem = (
                    "Cadastro realizado com sucesso! Seu acesso está em análise e "
                    "aguardando liberação do administrador."
                )

    alerta_html = ""
    if erro:
        alerta_html = f'<div style="background:rgba(231,76,60,0.18); color:#fc8181; padding:10px 12px; border-radius:6px; margin-bottom:14px; font-size:13px;">{erro}</div>'
    elif mensagem:
        alerta_html = f'<div style="background:rgba(72,187,120,0.18); color:#68d391; padding:10px 12px; border-radius:6px; margin-bottom:14px; font-size:13px;">{mensagem}</div>'

    return render_template_string(
        f"""
        <!DOCTYPE html>
        <html lang="pt-br">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Cadastro - Gestão de Obras</title>
            <style>{BASE_CSS}</style>
        </head>
        <body>
            <div class="login-container">
                <div class="login-box">
                    <div style="margin-bottom:20px; border-bottom:1px solid #2d3748; padding-bottom:15px;">
                        <h2 style="margin:0; font-size:16px; color:#d4af37;">SOLICITAR ACESSO</h2>
                        <span style="font-size:12px; color:#a0aec0;">Cadastro de Profissional</span>
                    </div>
                    {alerta_html}
                    <form method="POST">
                        <label style="font-size:13px; color:#cbd5e0;">Nome Completo:</label>
                        <input type="text" name="nome" required>

                        <label style="font-size:13px; color:#cbd5e0;">WhatsApp (com DDD):</label>
                        <input type="text" name="whatsapp" placeholder="Ex: (17) 99999-9999" required>

                        <label style="font-size:13px; color:#cbd5e0;">Usuário de Acesso:</label>
                        <input type="text" name="usuario" autocomplete="username" required>

                        <label style="font-size:13px; color:#cbd5e0;">Senha:</label>
                        <input type="password" name="senha" autocomplete="new-password" required>

                        <button type="submit" class="btn" style="width:100%; margin-top:8px;">Cadastrar e Enviar para Análise</button>
                    </form>
                    <div style="margin-top:16px; text-align:center; font-size:13px;">
                        <a href="/login" style="color:#d4af37; text-decoration:none;">Já tem cadastro? Fazer Login</a>
                    </div>
                </div>
            </div>
            <div class="watermark-had">HAD</div>
        </body>
        </html>
    """
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    aviso = ""
    tipo_aviso = "erro"
    proximo = request.args.get("next") or request.form.get("next") or url_for("index")
    if not proximo.startswith("/"):
        proximo = url_for("index")

    if request.method == "POST":
        usuario = (request.form.get("usuario") or "").strip().lower()
        senha = request.form.get("senha") or ""

        if usuario == ADMIN_USER.lower() and senha == ADMIN_PASS:
            session.clear()
            session["logado"] = True
            session["is_admin"] = True
            session["usuario"] = ADMIN_USER
            session["status_usuario"] = "ativo"
            return redirect(url_for("admin_painel"))

        conn = get_db_connection()
        user_row = conn.execute(
            "SELECT * FROM usuarios WHERE usuario = ?", (usuario,)
        ).fetchone()
        conn.close()

        if user_row and check_password_hash(user_row["senha_hash"], senha):
            status = (user_row["status"] or "pendente").lower()
            if status == "ativo":
                session.clear()
                session["logado"] = True
                session["is_admin"] = False
                session["usuario_id"] = user_row["id"]
                session["usuario"] = user_row["usuario"]
                session["nome_usuario"] = user_row["nome"]
                session["status_usuario"] = "ativo"
                return redirect(proximo)
            elif status == "pendente":
                aviso = "Seu cadastro está em análise e aguardando a liberação do administrador."
                tipo_aviso = "alerta"
            elif status == "bloqueado":
                aviso = "Acesso trancado por pendência na mensalidade. Entre em contato para regularizar."
                tipo_aviso = "erro"
            else:
                aviso = "Acesso indisponível no momento."
                tipo_aviso = "erro"
        else:
            aviso = "Usuário ou senha inválidos."
            tipo_aviso = "erro"

    aviso_html = ""
    if aviso:
        if tipo_aviso == "alerta":
            aviso_html = f'<div style="background:rgba(212,175,55,0.18); color:#f6e05e; padding:10px 12px; border-radius:6px; margin-bottom:14px; font-size:13px;">{aviso}</div>'
        else:
            aviso_html = f'<div style="background:rgba(231,76,60,0.18); color:#fc8181; padding:10px 12px; border-radius:6px; margin-bottom:14px; font-size:13px;">{aviso}</div>'

    return render_template_string(
        f"""
        <!DOCTYPE html>
        <html lang="pt-br">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Login - Gestão de Obras</title>
            <style>{BASE_CSS}</style>
        </head>
        <body>
            <div class="login-container">
                <div class="login-box">
                    <div style="margin-bottom:20px; border-bottom:1px solid #2d3748; padding-bottom:15px;">
                        <h2 style="margin:0; font-size:16px; color:#d4af37;">GESTÃO DE OBRAS</h2>
                        <span style="font-size:12px; color:#a0aec0;">Acesso ao Painel</span>
                    </div>
                    {aviso_html}
                    <form method="POST">
                        <input type="hidden" name="next" value="{proximo}">
                        <label style="font-size:13px; color:#cbd5e0;">Usuário:</label>
                        <input type="text" name="usuario" autocomplete="username" required>
                        <label style="font-size:13px; color:#cbd5e0;">Senha:</label>
                        <input type="password" name="senha" autocomplete="current-password" required>
                        <div style="text-align:right; margin-top:-4px; margin-bottom:10px;">
                            <a href="/esqueci_senha" style="color:#a0aec0; font-size:12px; text-decoration:none;">Esqueceu sua senha?</a>
                        </div>
                        <button type="submit" class="btn" style="width:100%; margin-top:4px;">Entrar</button>
                    </form>
                    <div style="margin-top:16px; text-align:center; font-size:13px;">
                        <a href="/cadastro" style="color:#d4af37; text-decoration:none;">Novo profissional? Solicitar cadastro</a>
                    </div>
                </div>
            </div>
            <div class="watermark-had">HAD</div>
        </body>
        </html>
    """
    )


@app.route("/esqueci_senha", methods=["GET", "POST"])
def esqueci_senha():
    erro = ""
    link_redefinicao = ""
    usuario_encontrado = ""

    if request.method == "POST":
        identificador = (request.form.get("identificador") or "").strip()
        if not identificador:
            erro = "Informe seu usuário ou número de WhatsApp cadastrado."
        else:
            usuario_limpo = identificador.lower()
            digitos_fone = re.sub(r"\D", "", identificador)

            conn = get_db_connection()
            user_row = conn.execute(
                "SELECT id, nome, usuario, whatsapp, senha_hash FROM usuarios WHERE LOWER(usuario) = ?",
                (usuario_limpo,),
            ).fetchone()

            if not user_row and digitos_fone:
                todos = conn.execute(
                    "SELECT id, nome, usuario, whatsapp, senha_hash FROM usuarios"
                ).fetchall()
                for u in todos:
                    if re.sub(r"\D", "", u["whatsapp"] or "") == digitos_fone:
                        user_row = u
                        break
            conn.close()

            if not user_row:
                erro = "Não encontramos nenhum cadastro com esse usuário ou WhatsApp."
            else:
                token = _gerar_token_redefinicao(
                    user_row["id"], user_row["senha_hash"] or ""
                )
                caminho_relativo = url_for("redefinir_senha", token=token)
                host_base = request.host_url.rstrip("/")
                link_redefinicao = f"{host_base}{caminho_relativo}"
                usuario_encontrado = f"{user_row['nome']} (@{user_row['usuario']})"

    alerta_html = ""
    if erro:
        alerta_html = f'<div style="background:rgba(231,76,60,0.18); color:#fc8181; padding:10px 12px; border-radius:6px; margin-bottom:14px; font-size:13px;">{erro}</div>'

    link_box_html = ""
    if link_redefinicao:
        link_box_html = f"""
            <div style="background:rgba(72,187,120,0.14); border:1px solid #48bb78; color:#e2e8f0; padding:12px; border-radius:6px; margin-bottom:14px; font-size:12px;">
                <div style="color:#68d391; font-weight:700; margin-bottom:6px;">Link gerado para {usuario_encontrado} (válido por 30 min):</div>
                <div style="word-break:break-all; background:#111318; padding:8px; border-radius:4px; margin-bottom:10px;">
                    <a href="{link_redefinicao}" style="color:#d4af37; text-decoration:underline;">{link_redefinicao}</a>
                </div>
                <a href="{link_redefinicao}" class="btn" style="width:100%;">Redefinir Senha Agora</a>
            </div>
        """

    return render_template_string(
        f"""
        <!DOCTYPE html>
        <html lang="pt-br">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Esqueceu sua Senha - Gestão de Obras</title>
            <style>{BASE_CSS}</style>
        </head>
        <body>
            <div class="login-container">
                <div class="login-box">
                    <div style="margin-bottom:20px; border-bottom:1px solid #2d3748; padding-bottom:15px;">
                        <h2 style="margin:0; font-size:16px; color:#d4af37;">RECUPERAR SENHA</h2>
                        <span style="font-size:12px; color:#a0aec0;">Informe seu usuário ou WhatsApp</span>
                    </div>
                    {alerta_html}
                    {link_box_html}
                    <form method="POST">
                        <label style="font-size:13px; color:#cbd5e0;">Usuário ou WhatsApp:</label>
                        <input type="text" name="identificador" placeholder="Ex: meu_usuario ou (17) 99999-9999" required>
                        <button type="submit" class="btn" style="width:100%; margin-top:8px;">Gerar Link de Redefinição</button>
                    </form>
                    <div style="margin-top:16px; text-align:center; font-size:13px;">
                        <a href="/login" style="color:#d4af37; text-decoration:none;">← Voltar para o Login</a>
                    </div>
                </div>
            </div>
            <div class="watermark-had">HAD</div>
        </body>
        </html>
    """
    )


@app.route("/redefinir_senha/<token>", methods=["GET", "POST"])
def redefinir_senha(token):
    erro = ""
    sucesso = ""
    token_valido = False
    user_row = None

    dados_token, status_token = _validar_token_redefinicao(token)
    if status_token == "expired":
        erro = "Este link expirou (prazo de 30 minutos). Solicite um novo link."
    elif status_token != "ok" or not dados_token:
        erro = "Link de redefinição inválido."
    else:
        conn = get_db_connection()
        user_row = conn.execute(
            "SELECT id, nome, usuario, senha_hash FROM usuarios WHERE id = ?",
            (dados_token.get("uid"),),
        ).fetchone()
        conn.close()

        if user_row and (user_row["senha_hash"] or "")[:16] == dados_token.get("p"):
            token_valido = True
        else:
            erro = "Este link de redefinição já foi utilizado ou é inválido."

    if token_valido and request.method == "POST":
        nova_senha = request.form.get("nova_senha") or ""
        confirmar_senha = request.form.get("confirmar_senha") or ""

        if len(nova_senha) < 4:
            erro = "A nova senha deve ter pelo menos 4 caracteres."
        elif nova_senha != confirmar_senha:
            erro = "A confirmação da senha não confere."
        else:
            conn = get_db_connection()
            conn.execute(
                "UPDATE usuarios SET senha_hash = ? WHERE id = ?",
                (generate_password_hash(nova_senha), user_row["id"]),
            )
            conn.commit()
            conn.close()
            sucesso = "Senha redefinida com sucesso! Você já pode fazer login."
            token_valido = False

    alerta_html = ""
    if erro:
        alerta_html = f'<div style="background:rgba(231,76,60,0.18); color:#fc8181; padding:10px 12px; border-radius:6px; margin-bottom:14px; font-size:13px;">{erro}</div>'
    elif sucesso:
        alerta_html = f'<div style="background:rgba(72,187,120,0.18); color:#68d391; padding:10px 12px; border-radius:6px; margin-bottom:14px; font-size:13px;">{sucesso}</div>'

    form_html = ""
    if token_valido and user_row:
        form_html = f"""
            <p style="font-size:12px; color:#a0aec0; margin-top:0;">Conta: <strong style="color:#fff;">{user_row["nome"]}</strong> (@{user_row["usuario"]})</p>
            <form method="POST">
                <label style="font-size:13px; color:#cbd5e0;">Nova Senha:</label>
                <input type="password" name="nova_senha" autocomplete="new-password" required>

                <label style="font-size:13px; color:#cbd5e0;">Confirmar Nova Senha:</label>
                <input type="password" name="confirmar_senha" autocomplete="new-password" required>

                <button type="submit" class="btn" style="width:100%; margin-top:8px;">Salvar Nova Senha</button>
            </form>
        """
    elif not sucesso:
        form_html = """
            <div style="text-align:center; margin-top:10px;">
                <a href="/esqueci_senha" class="btn" style="width:100%;">Solicitar Novo Link</a>
            </div>
        """

    return render_template_string(
        f"""
        <!DOCTYPE html>
        <html lang="pt-br">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Redefinir Senha - Gestão de Obras</title>
            <style>{BASE_CSS}</style>
        </head>
        <body>
            <div class="login-container">
                <div class="login-box">
                    <div style="margin-bottom:20px; border-bottom:1px solid #2d3748; padding-bottom:15px;">
                        <h2 style="margin:0; font-size:16px; color:#d4af37;">NOVA SENHA</h2>
                        <span style="font-size:12px; color:#a0aec0;">Defina sua nova senha de acesso</span>
                    </div>
                    {alerta_html}
                    {form_html}
                    <div style="margin-top:16px; text-align:center; font-size:13px;">
                        <a href="/login" style="color:#d4af37; text-decoration:none;">Ir para o Login</a>
                    </div>
                </div>
            </div>
            <div class="watermark-had">HAD</div>
        </body>
        </html>
    """
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin_painel():
    conn = get_db_connection()

    if request.method == "POST":
        acao = request.form.get("acao")
        user_id = request.form.get("user_id")
        if user_id:
            if acao == "liberar":
                conn.execute(
                    "UPDATE usuarios SET status = 'ativo' WHERE id = ?", (user_id,)
                )
            elif acao == "trancar":
                conn.execute(
                    "UPDATE usuarios SET status = 'bloqueado' WHERE id = ?", (user_id,)
                )
            elif acao == "salvar_vencimento":
                novo_venc = (request.form.get("vencimento") or "").strip()
                conn.execute(
                    "UPDATE usuarios SET vencimento = ? WHERE id = ?",
                    (novo_venc, user_id),
                )
            elif acao == "excluir_usuario":
                user_alvo = conn.execute(
                    "SELECT id, logo_arquivo FROM usuarios WHERE id = ?", (user_id,)
                ).fetchone()
                if user_alvo:
                    if user_alvo["logo_arquivo"]:
                        caminho_logo = os.path.join(
                            app.config["LOGOS_FOLDER"],
                            os.path.basename(user_alvo["logo_arquivo"]),
                        )
                        if os.path.exists(caminho_logo):
                            try:
                                os.remove(caminho_logo)
                            except OSError:
                                pass

                    pdfs_user = conn.execute(
                        "SELECT pdf_projeto FROM obras WHERE usuario_id = ? AND pdf_projeto IS NOT NULL",
                        (user_id,),
                    ).fetchall()
                    for p in pdfs_user:
                        if p["pdf_projeto"]:
                            caminho_pdf = os.path.join(
                                app.config["UPLOAD_FOLDER"],
                                os.path.basename(p["pdf_projeto"]),
                            )
                            if os.path.exists(caminho_pdf):
                                try:
                                    os.remove(caminho_pdf)
                                except OSError:
                                    pass

                    conn.execute("DELETE FROM usuarios WHERE id = ?", (user_id,))
            conn.commit()
        conn.close()
        return redirect(url_for("admin_painel"))

    usuarios = conn.execute("SELECT * FROM usuarios ORDER BY id DESC").fetchall()
    conn.close()

    rows_html = ""
    if usuarios:
        for u in usuarios:
            st = (u["status"] or "pendente").lower()
            if st == "ativo":
                badge = '<span style="color:#48bb78; font-weight:700;">Ativo</span>'
            elif st == "bloqueado":
                badge = '<span style="color:#fc8181; font-weight:700;">Trancado</span>'
            else:
                badge = '<span style="color:#f6e05e; font-weight:700;">Em Análise</span>'

            empresa_txt = (u["nome_empresa"] or "").strip()
            sub_info = (
                f"@{u['usuario']} · {empresa_txt}"
                if empresa_txt
                else f"@{u['usuario']}"
            )
            msg_cobranca = montar_mensagem_cobranca(u["nome"], u["vencimento"])
            msg_escapada = (
                msg_cobranca.replace("\\", "\\\\")
                .replace("'", "\\'")
                .replace("\n", "\\n")
            )
            nome_escapado = (u["nome"] or "").replace("\\", "\\\\").replace("'", "\\'")
            fone_wa = normalizar_whatsapp(u["whatsapp"])
            wa_link = (
                f"https://wa.me/{fone_wa}?text={msg_cobranca.replace(' ', '%20')}"
                if fone_wa
                else "#"
            )

            rows_html += f"""
                <tr>
                    <td>
                        <strong style="color:#fff;">{u["nome"]}</strong><br>
                        <span style="font-size:11px; color:#a0aec0;">{sub_info}</span>
                    </td>
                    <td>{u["whatsapp"]}</td>
                    <td>{badge}</td>
                    <td>
                        <form method="post" style="display:flex; gap:6px; align-items:center; margin:0;">
                            <input type="hidden" name="acao" value="salvar_vencimento">
                            <input type="hidden" name="user_id" value="{u["id"]}">
                            <input type="date" name="vencimento" value="{u["vencimento"] or ""}" style="width:140px; margin:0; padding:5px; font-size:12px;">
                            <button type="submit" class="btn-detalhe">Salvar</button>
                        </form>
                    </td>
                    <td>
                        <form method="post" style="display:inline; margin:0;">
                            <input type="hidden" name="acao" value="liberar">
                            <input type="hidden" name="user_id" value="{u["id"]}">
                            <button type="submit" class="btn-detalhe" style="color:#48bb78;">Liberar</button>
                        </form>
                        <form method="post" style="display:inline; margin:0;">
                            <input type="hidden" name="acao" value="trancar">
                            <input type="hidden" name="user_id" value="{u["id"]}">
                            <button type="submit" class="btn-excluir">Trancar</button>
                        </form>
                        <button type="button" class="btn-detalhe" style="color:#d4af37;" onclick="prepararCobranca('{nome_escapado}', '{msg_escapada}', '{wa_link}')">
                            Cobrança WA
                        </button>
                        <form method="post" style="display:inline; margin:0;" onsubmit="return confirm('Excluir permanentemente o usuário {nome_escapado} e todos os seus dados?')">
                            <input type="hidden" name="acao" value="excluir_usuario">
                            <input type="hidden" name="user_id" value="{u["id"]}">
                            <button type="submit" class="btn-excluir">Excluir</button>
                        </form>
                    </td>
                </tr>
            """
    else:
        rows_html = '<tr><td colspan="5" style="text-align:center; color:#a0aec0;">Nenhum profissional cadastrado ainda.</td></tr>'

    return render_template_string(
        f"""
        <!DOCTYPE html>
        <html lang="pt-br">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Administração - Controle de Acessos</title>
            <style>{BASE_CSS}</style>
        </head>
        <body>
            <div class="main-wrapper" style="margin-left:0; width:100%;">
                <div class="top-navbar">
                    <span style="font-weight: 600; font-size: 13px; letter-spacing: 0.5px; color: #d4af37;">PAINEL ADMINISTRATIVO</span>
                    <div class="user-info">
                        <a href="/logout" class="btn-excluir" style="margin:0;">Sair</a>
                    </div>
                </div>
                <div class="content-area">
                    <div class="top-bar-title">
                        <div>
                            <h1>Controle de Assinaturas e Acessos</h1>
                            <p>Aprovação de cadastros, bloqueio por pendência, vencimento, exclusão e cobrança via WhatsApp.</p>
                        </div>
                    </div>

                    <div class="card" id="box-cobranca" style="display:none; border-left: 5px solid #d4af37;">
                        <h3 style="margin-top:0;">Mensagem Amigável de Cobrança (<span id="cob-nome"></span>)</h3>
                        <textarea id="cob-texto" rows="4" style="width:100%; background:#111318; color:#fff; border:1px solid #2d3748; border-radius:6px; padding:10px; font-size:13px;"></textarea>
                        <div style="display:flex; gap:10px; flex-wrap:wrap; margin-top:12px;">
                            <button type="button" class="btn" onclick="copiarCobranca()">Copiar Mensagem</button>
                            <a id="cob-wa-link" href="#" target="_blank" class="btn btn-secundario">Abrir no WhatsApp</a>
                            <button type="button" class="btn-detalhe" onclick="document.getElementById('box-cobranca').style.display='none'">Fechar</button>
                        </div>
                    </div>

                    <div class="card">
                        <h3 style="margin-top:0;">Profissionais Cadastrados</h3>
                        <table>
                            <thead>
                                <tr>
                                    <th>Profissional</th>
                                    <th>WhatsApp</th>
                                    <th>Status</th>
                                    <th>Vencimento</th>
                                    <th>Ações</th>
                                </tr>
                            </thead>
                            <tbody>{rows_html}</tbody>
                        </table>
                    </div>
                </div>
            </div>
            <div class="watermark-had">HAD</div>

            <script>
            function prepararCobranca(nome, msg, waLink) {{
                document.getElementById('cob-nome').textContent = nome;
                document.getElementById('cob-texto').value = msg;
                const linkEl = document.getElementById('cob-wa-link');
                linkEl.href = waLink;
                linkEl.style.display = waLink && waLink !== '#' ? 'inline-block' : 'none';
                const box = document.getElementById('box-cobranca');
                box.style.display = 'block';
                box.scrollIntoView({{ behavior: 'smooth' }});
            }}

            function copiarCobranca() {{
                const txt = document.getElementById('cob-texto');
                if (navigator.clipboard && navigator.clipboard.writeText) {{
                    navigator.clipboard.writeText(txt.value).then(() => {{
                        alert('Mensagem copiada! Agora é só colar e enviar no WhatsApp.');
                    }}).catch(() => {{
                        txt.select();
                        document.execCommand('copy');
                        alert('Mensagem copiada! Agora é só colar e enviar no WhatsApp.');
                    }});
                }} else {{
                    txt.select();
                    document.execCommand('copy');
                    alert('Mensagem copiada! Agora é só colar e enviar no WhatsApp.');
                }}
            }}
            </script>
        </body>
        </html>
    """
    )


@app.route("/logos/<filename>")
@login_required
def uploaded_logo(filename):
    uid = current_usuario_id()
    safe_name = os.path.basename(filename)
    conn = get_db_connection()
    user_row = conn.execute(
        "SELECT id FROM usuarios WHERE id = ? AND logo_arquivo = ?",
        (uid, safe_name),
    ).fetchone()
    conn.close()
    if not user_row:
        return redirect(url_for("index"))
    return send_from_directory(app.config["LOGOS_FOLDER"], safe_name)


@app.route("/minha_empresa", methods=["GET", "POST"])
@login_required
def minha_empresa():
    uid = current_usuario_id()
    conn = get_db_connection()
    user_row = conn.execute(
        "SELECT id, nome_empresa, logo_arquivo FROM usuarios WHERE id = ?", (uid,)
    ).fetchone()

    if not user_row:
        conn.close()
        return redirect(url_for("login"))

    mensagem = ""
    erro = ""

    if request.method == "POST":
        novo_nome_empresa = (request.form.get("nome_empresa") or "").strip()
        logo_atual = user_row["logo_arquivo"]

        if request.form.get("limpar_identidade") == "1":
            if logo_atual:
                caminho_antigo = os.path.join(
                    app.config["LOGOS_FOLDER"], os.path.basename(logo_atual)
                )
                if os.path.exists(caminho_antigo):
                    try:
                        os.remove(caminho_antigo)
                    except OSError:
                        pass
            novo_nome_empresa = ""
            logo_atual = None

        elif "logo_arquivo" in request.files:
            arquivo = request.files["logo_arquivo"]
            if arquivo and arquivo.filename:
                ext = os.path.splitext(arquivo.filename)[1].lower()
                if ext not in EXTENSOES_LOGO:
                    erro = "Envie uma imagem válida em PNG, JPG, JPEG ou WEBP."
                else:
                    safe_base = (
                        secure_filename(
                            os.path.splitext(os.path.basename(arquivo.filename))[0]
                        )
                        or "logo"
                    )
                    nome_salvo = f"u{uid}_{safe_base}{ext}"
                    arquivo.save(os.path.join(app.config["LOGOS_FOLDER"], nome_salvo))
                    logo_atual = nome_salvo

        if not erro:
            valor_empresa_db = novo_nome_empresa if novo_nome_empresa else None
            conn.execute(
                "UPDATE usuarios SET nome_empresa = ?, logo_arquivo = ? WHERE id = ?",
                (valor_empresa_db, logo_atual, uid),
            )
            conn.commit()
            mensagem = "Identidade da empresa atualizada com sucesso."

    conn.close()

    empresa_exibida, logo_exibida = obter_branding_usuario()
    nome_exibicao = empresa_exibida if empresa_exibida else "?"
    logo_preview_html = (
        f'<img src="{logo_exibida}" alt="Logo Atual" style="width:52px; height:52px; border-radius:6px; object-fit:cover; border:1px solid #d4af37;">'
        if logo_exibida
        else '<div class="logo-placeholder-had" style="width:52px; height:52px; font-size:18px;">?</div>'
    )
    aviso_pendente_html = ""
    if not empresa_exibida or not logo_exibida:
        aviso_pendente_html = (
            '<div style="background:rgba(212,175,55,0.12); border:1px dashed #d4af37; color:#f6e05e; '
            'padding:10px 12px; border-radius:6px; margin-bottom:14px; font-size:12px;">'
            '<strong>? Identidade não configurada:</strong> Defina o nome da sua empresa/escritório e envie seu logotipo abaixo.'
            '</div>'
        )

    alerta_html = ""
    if erro:
        alerta_html = f'<div style="background:rgba(231,76,60,0.18); color:#fc8181; padding:10px 12px; border-radius:6px; margin-bottom:14px; font-size:13px;">{erro}</div>'
    elif mensagem:
        alerta_html = f'<div style="background:rgba(72,187,120,0.18); color:#68d391; padding:10px 12px; border-radius:6px; margin-bottom:14px; font-size:13px;">{mensagem}</div>'

    body = f"""
        <a href="/" class="voltar">← Voltar para o Início</a>
        <div class="card" style="max-width: 650px;">
            <h2 style="margin-top:0;">Identidade da Empresa</h2>
            <p style="color:#a0aec0; font-size:13px;">Personalize o nome e o logotipo exibidos no seu painel operacional.</p>
            {alerta_html}
            {aviso_pendente_html}

            <div style="display:flex; align-items:center; gap:14px; padding:14px; background:#111318; border:1px solid #2d3748; border-radius:8px; margin-bottom:18px;">
                {logo_preview_html}
                <div>
                    <div style="font-size:11px; color:#a0aec0; text-transform:uppercase;">Exibição Atual</div>
                    <div style="font-size:15px; font-weight:700; color:#d4af37;">{nome_exibicao}</div>
                </div>
            </div>

            <form method="post" enctype="multipart/form-data">
                <label>Nome da Empresa / Escritório:</label>
                <input type="text" name="nome_empresa" value="{empresa_exibida}" placeholder="Ex: Nome da sua empresa ou escritório">

                <label>Logotipo (PNG, JPG, JPEG ou WEBP):</label>
                <input type="file" name="logo_arquivo" accept=".png,.jpg,.jpeg,.webp">

                <div style="display:flex; gap:10px; flex-wrap:wrap; margin-top:10px;">
                    <button type="submit" class="btn">Salvar Identidade</button>
                </div>
            </form>

            <form method="post" style="margin-top:14px;">
                <input type="hidden" name="limpar_identidade" value="1">
                <button type="submit" class="btn btn-secundario">Limpar Identidade (?)</button>
            </form>
        </div>
    """
    return render_layout(
        body,
        active_menu="empresa",
        nome_empresa=empresa_exibida,
        logo_url=logo_exibida,
    )


@app.route("/")
@login_required
def index():
    uid = current_usuario_id()
    conn = get_db_connection()
    obras_cursor = conn.execute(
        """
        SELECT obras.*, clientes.nome as cliente_nome
        FROM obras
        LEFT JOIN clientes ON obras.cliente_id = clientes.id AND clientes.usuario_id = obras.usuario_id
        WHERE obras.usuario_id = ?
        ORDER BY obras.id DESC
    """,
        (uid,),
    ).fetchall()

    obras = []
    total_custo_geral = 0.0
    lucro_geral_obras = 0.0

    for o in obras_cursor:
        obra_dict = dict(o)
        mat_row = conn.execute(
            "SELECT SUM(valor_total) as total FROM materiais_obra WHERE obra_id = ?",
            (o["id"],),
        ).fetchone()
        custo_materiais = float(mat_row["total"] or 0.0)
        terreno = float(o["valor_terreno"] or 0.0)
        financiamento = float(o["valor_financiamento"] or 0.0)
        entrada = float(o["valor_entrada"] or 0.0)

        custo_obra = custo_materiais + terreno
        receita_obra = financiamento + entrada
        lucro_obra = receita_obra - custo_obra

        obra_dict["custo_obra"] = custo_obra
        obra_dict["lucro_obra"] = lucro_obra

        total_custo_geral += custo_obra
        lucro_geral_obras += lucro_obra
        obras.append(obra_dict)

    total_obras = len(obras)
    conn.close()

    lucro_cor = "#48bb78" if lucro_geral_obras >= 0 else "#fc8181"

    linhas_html = ""
    if obras:
        for obra in obras:
            if obra["pdf_projeto"]:
                pdf_link = f'<a href="/uploads/{obra["pdf_projeto"]}" target="_blank" style="color: #d4af37; font-weight: bold;">Ver PDF</a>'
            else:
                pdf_link = '<span style="color: #718096;">Sem Projeto</span>'

            lucro_item_cor = "#48bb78" if obra["lucro_obra"] >= 0 else "#fc8181"
            linhas_html += f"""
                <tr>
                    <td>{obra["id"]:02d}</td>
                    <td><strong style="color: #ffffff;">{obra["nome_obra"]}</strong></td>
                    <td>{obra["cliente_nome"] or "Sem Cliente"}</td>
                    <td>{format_brl(obra["valor_financiamento"])}</td>
                    <td>{format_num_br(obra["metro_quadrado"], 2)} m²</td>
                    <td style="color: {lucro_item_cor}; font-weight: 600;">{format_brl(obra["lucro_obra"])}</td>
                    <td>{pdf_link}</td>
                    <td>
                        <a href="/obra/{obra["id"]}" class="btn-detalhe">Orçamento</a>
                        <a href="/editar_obra/{obra["id"]}" class="btn-detalhe" style="color: #d4af37;">Editar</a>
                        <a href="/excluir_obra/{obra["id"]}" class="btn-excluir" onclick="return confirm('Deseja excluir esta obra?')">Excluir</a>
                    </td>
                </tr>
            """
    else:
        linhas_html = '<tr><td colspan="8" style="text-align: center; color: #a0aec0;">Nenhuma obra registada até o momento.</td></tr>'

    body = f"""
        <div class="top-bar-title">
            <div>
                <h1>Resumo Geral das Obras</h1>
                <p>Acompanhamento financeiro e orçamental das construções.</p>
            </div>
            <div>
                <a href="/nova_obra" class="btn">+ Registar Obra</a>
            </div>
        </div>

        <div class="metrics-container">
            <div class="metric-card">
                <h4>Obras Ativas</h4>
                <div class="value">{total_obras}</div>
            </div>
            <div class="metric-card">
                <h4>Orçamento Total Gasto</h4>
                <div class="value" style="color: #48bb78;">{format_brl(total_custo_geral)}</div>
            </div>
            <div class="metric-card">
                <h4>Lucro Geral das Obras</h4>
                <div class="value" style="color: {lucro_cor};">{format_brl(lucro_geral_obras)}</div>
            </div>
        </div>

        <div class="card">
            <div class="card-header-toggle" onclick="toggleSection(this, 'sec-obras-lista')">
                <h3 style="margin: 0; font-size: 16px;">Lista de Obras em Andamento</h3>
                <span class="toggle-arrow">▾ Fechar</span>
            </div>
            <div id="sec-obras-lista" class="collapsible-body">
                <table>
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Nome da Obra</th>
                            <th>Cliente</th>
                            <th>Financiamento</th>
                            <th>Área (m²)</th>
                            <th>Lucro da Obra</th>
                            <th>Projeto PDF</th>
                            <th>Ações</th>
                        </tr>
                    </thead>
                    <tbody>
                        {linhas_html}
                    </tbody>
                </table>
            </div>
        </div>
    """
    return render_layout(body, active_menu="resumo")


@app.route("/novo_cliente", methods=["GET", "POST"])
@login_required
def novo_cliente():
    uid = current_usuario_id()
    conn = get_db_connection()
    if request.method == "POST":
        nome = request.form["nome"].strip()
        telefone = request.form["telefone"].strip()
        endereco = request.form["endereco"].strip()

        conn.execute(
            "INSERT INTO clientes (usuario_id, nome, telefone, endereco) VALUES (?, ?, ?, ?)",
            (uid, nome, telefone, endereco),
        )
        conn.commit()
        conn.close()
        return redirect(url_for("novo_cliente"))

    clientes = conn.execute(
        "SELECT * FROM clientes WHERE usuario_id = ? ORDER BY id DESC", (uid,)
    ).fetchall()
    conn.close()

    cli_rows = ""
    if clientes:
        for c in clientes:
            cli_rows += f"""
                <tr>
                    <td><strong style="color:#fff;">{c["nome"]}</strong></td>
                    <td>{c["telefone"] or "-"}</td>
                    <td>{c["endereco"] or "-"}</td>
                </tr>
            """
    else:
        cli_rows = '<tr><td colspan="3" style="text-align:center; color:#a0aec0;">Nenhum cliente registado.</td></tr>'

    body = f"""
        <a href="/" class="voltar">← Voltar para o Início</a>
        <div class="card" style="max-width: 650px;">
            <div class="card-header-toggle" onclick="toggleSection(this, 'sec-form-cliente')">
                <h2 style="margin:0;">Registar Novo Cliente</h2>
                <span class="toggle-arrow">▾ Fechar</span>
            </div>
            <div id="sec-form-cliente" class="collapsible-body">
                <form method="post" style="margin-top: 15px;">
                    <label>Nome:</label>
                    <input type="text" name="nome" autocomplete="off" required>
                    <label>Telefone:</label>
                    <input type="text" name="telefone" autocomplete="off">
                    <label>Endereço:</label>
                    <input type="text" name="endereco" autocomplete="off">
                    <button type="submit" class="btn" style="width: 100%; margin-top: 10px;">Salvar Cliente</button>
                </form>
            </div>
        </div>

        <div class="card">
            <div class="card-header-toggle" onclick="toggleSection(this, 'sec-lista-clientes')">
                <h3 style="margin:0;">Clientes Cadastrados</h3>
                <span class="toggle-arrow">▾ Fechar</span>
            </div>
            <div id="sec-lista-clientes" class="collapsible-body">
                <table>
                    <thead>
                        <tr>
                            <th>Nome</th>
                            <th>Telefone</th>
                            <th>Endereço</th>
                        </tr>
                    </thead>
                    <tbody>{cli_rows}</tbody>
                </table>
            </div>
        </div>
    """
    return render_layout(body, active_menu="clientes")


@app.route("/nova_obra", methods=["GET", "POST"])
@login_required
def nova_obra():
    uid = current_usuario_id()
    conn = get_db_connection()
    if request.method == "POST":
        cliente_id = request.form.get("cliente_id") or None
        if cliente_id:
            cli_ok = conn.execute(
                "SELECT id FROM clientes WHERE id = ? AND usuario_id = ?",
                (cliente_id, uid),
            ).fetchone()
            if not cli_ok:
                cliente_id = None

        nome_obra = request.form["nome_obra"].strip()
        valor_financiamento = parse_moeda(request.form.get("valor_financiamento"))
        valor_entrada = parse_moeda(request.form.get("valor_entrada"))
        valor_terreno = parse_moeda(request.form.get("valor_terreno"))
        metro_quadrado = parse_moeda(request.form.get("metro_quadrado"))
        valor_m2_usuario = parse_moeda(request.form.get("valor_m2_usuario"))

        pdf_filename = None
        if "pdf_projeto" in request.files:
            file = request.files["pdf_projeto"]
            if file and file.filename and file.filename.lower().endswith(".pdf"):
                safe_name = secure_filename(os.path.basename(file.filename))
                if safe_name:
                    pdf_filename = f"u{uid}_{safe_name}"
                    file.save(os.path.join(app.config["UPLOAD_FOLDER"], pdf_filename))

        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO obras (
                usuario_id, cliente_id, nome_obra, valor_financiamento, valor_entrada,
                valor_terreno, metro_quadrado, valor_m2_usuario, pdf_projeto
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                uid,
                cliente_id,
                nome_obra,
                valor_financiamento,
                valor_entrada,
                valor_terreno,
                metro_quadrado,
                valor_m2_usuario,
                pdf_filename,
            ),
        )
        obra_id = cursor.lastrowid

        for etapa in ETAPAS_PADRAO:
            conn.execute(
                "INSERT INTO etapas_obra (obra_id, nome_etapa, percentual) VALUES (?, ?, 0)",
                (obra_id, etapa),
            )

        conn.commit()
        conn.close()
        return redirect(url_for("index"))

    clientes = conn.execute(
        "SELECT * FROM clientes WHERE usuario_id = ? ORDER BY nome", (uid,)
    ).fetchall()
    conn.close()

    options = "".join(
        [f'<option value="{c["id"]}">{c["nome"]}</option>' for c in clientes]
    )

    body = f"""
        <a href="/" class="voltar">← Voltar para o Início</a>
        <div class="card" style="max-width: 650px;">
            <div class="card-header-toggle" onclick="toggleSection(this, 'sec-nova-obra')">
                <h2 style="margin:0;">Registar Nova Obra</h2>
                <span class="toggle-arrow">▾ Fechar</span>
            </div>
            <div id="sec-nova-obra" class="collapsible-body">
                <form method="post" enctype="multipart/form-data" style="margin-top: 15px;">
                    <label>Cliente:</label>
                    <select name="cliente_id" required>
                        <option value="">Selecione um cliente</option>
                        {options}
                    </select>

                    <label>Nome da Obra:</label>
                    <input type="text" name="nome_obra" autocomplete="off" required>

                    <label>Valor Financiamento (R$):</label>
                    <input type="text" name="valor_financiamento" placeholder="Ex: 250000,00" autocomplete="off">

                    <label>Valor Entrada (R$):</label>
                    <input type="text" name="valor_entrada" placeholder="Ex: 50000,00" autocomplete="off">

                    <label>Valor do Terreno (R$):</label>
                    <input type="text" name="valor_terreno" placeholder="Ex: 80000,00" autocomplete="off">

                    <label>Área da Casa (m²):</label>
                    <input type="text" name="metro_quadrado" placeholder="Ex: 120,50" autocomplete="off">

                    <label>Valor por m² de Referência (R$/m²):</label>
                    <input type="text" name="valor_m2_usuario" placeholder="Ex: 2800,00" autocomplete="off">

                    <label>Projeto (PDF):</label>
                    <input type="file" name="pdf_projeto" accept=".pdf">

                    <button type="submit" class="btn" style="width: 100%; margin-top: 10px;">Salvar Obra</button>
                </form>
            </div>
        </div>
    """
    return render_layout(body, active_menu="obra")


@app.route("/editar_obra/<int:obra_id>", methods=["GET", "POST"])
@login_required
def editar_obra(obra_id):
    uid = current_usuario_id()
    conn = get_db_connection()
    obra = conn.execute(
        "SELECT * FROM obras WHERE id = ? AND usuario_id = ?", (obra_id, uid)
    ).fetchone()
    if not obra:
        conn.close()
        return redirect(url_for("index"))

    if request.method == "POST":
        cliente_id = request.form.get("cliente_id") or None
        if cliente_id:
            cli_ok = conn.execute(
                "SELECT id FROM clientes WHERE id = ? AND usuario_id = ?",
                (cliente_id, uid),
            ).fetchone()
            if not cli_ok:
                cliente_id = None

        nome_obra = request.form["nome_obra"].strip()
        valor_financiamento = parse_moeda(request.form.get("valor_financiamento"))
        valor_entrada = parse_moeda(request.form.get("valor_entrada"))
        valor_terreno = parse_moeda(request.form.get("valor_terreno"))
        metro_quadrado = parse_moeda(request.form.get("metro_quadrado"))
        valor_m2_usuario = parse_moeda(request.form.get("valor_m2_usuario"))

        pdf_filename = obra["pdf_projeto"]
        if "pdf_projeto" in request.files:
            file = request.files["pdf_projeto"]
            if file and file.filename and file.filename.lower().endswith(".pdf"):
                safe_name = secure_filename(os.path.basename(file.filename))
                if safe_name:
                    pdf_filename = f"u{uid}_{safe_name}"
                    file.save(os.path.join(app.config["UPLOAD_FOLDER"], pdf_filename))

        conn.execute(
            """
            UPDATE obras
            SET cliente_id = ?, nome_obra = ?, valor_financiamento = ?, valor_entrada = ?,
                valor_terreno = ?, metro_quadrado = ?, valor_m2_usuario = ?, pdf_projeto = ?
            WHERE id = ? AND usuario_id = ?
        """,
            (
                cliente_id,
                nome_obra,
                valor_financiamento,
                valor_entrada,
                valor_terreno,
                metro_quadrado,
                valor_m2_usuario,
                pdf_filename,
                obra_id,
                uid,
            ),
        )
        conn.commit()
        conn.close()
        return redirect(url_for("detalhe_obra", obra_id=obra_id))

    clientes = conn.execute(
        "SELECT * FROM clientes WHERE usuario_id = ? ORDER BY nome", (uid,)
    ).fetchall()
    conn.close()

    options = "".join(
        [
            f'<option value="{c["id"]}" {"selected" if c["id"] == obra["cliente_id"] else ""}>{c["nome"]}</option>'
            for c in clientes
        ]
    )

    body = f"""
        <a href="/obra/{obra_id}" class="voltar">← Voltar para Resumo da Obra</a>
        <div class="card" style="max-width: 650px;">
            <h2>Editar Dados da Obra</h2>
            <form method="post" enctype="multipart/form-data" style="margin-top: 15px;">
                <label>Cliente:</label>
                <select name="cliente_id">
                    <option value="">Sem cliente</option>
                    {options}
                </select>

                <label>Nome da Obra:</label>
                <input type="text" name="nome_obra" value="{obra["nome_obra"]}" required>

                <label>Valor Financiamento (R$):</label>
                <input type="text" name="valor_financiamento" value="{format_num_br(obra["valor_financiamento"], 2)}" required>

                <label>Valor Entrada (R$):</label>
                <input type="text" name="valor_entrada" value="{format_num_br(obra["valor_entrada"], 2)}" required>

                <label>Valor do Terreno (R$):</label>
                <input type="text" name="valor_terreno" value="{format_num_br(obra["valor_terreno"], 2)}" required>

                <label>Área da Casa (m²):</label>
                <input type="text" name="metro_quadrado" value="{format_num_br(obra["metro_quadrado"], 2)}" required>

                <label>Valor por m² de Referência (R$/m²):</label>
                <input type="text" name="valor_m2_usuario" value="{format_num_br(obra["valor_m2_usuario"], 2)}">

                <label>Substituir Projeto (PDF) — Atual: {obra["pdf_projeto"] or "Nenhum"}:</label>
                <input type="file" name="pdf_projeto" accept=".pdf">

                <div style="display:flex; gap:10px; flex-wrap:wrap; margin-top:10px;">
                    <button type="submit" class="btn">Salvar Alterações</button>
                    <a href="/obra/{obra_id}" class="btn btn-secundario">Cancelar</a>
                </div>
            </form>
        </div>
    """
    return render_layout(body, active_menu="resumo")


@app.route("/excluir_obra/<int:obra_id>")
@login_required
def excluir_obra(obra_id):
    uid = current_usuario_id()
    conn = get_db_connection()
    obra = conn.execute(
        "SELECT id FROM obras WHERE id = ? AND usuario_id = ?", (obra_id, uid)
    ).fetchone()
    if obra:
        conn.execute("DELETE FROM pagamentos_mo_obra WHERE obra_id = ?", (obra_id,))
        conn.execute("DELETE FROM materiais_obra WHERE obra_id = ?", (obra_id,))
        conn.execute("DELETE FROM etapas_obra WHERE obra_id = ?", (obra_id,))
        conn.execute("DELETE FROM obras WHERE id = ? AND usuario_id = ?", (obra_id, uid))
        conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/fornecedores", methods=["GET", "POST"])
@login_required
def fornecedores():
    uid = current_usuario_id()
    conn = get_db_connection()
    if request.method == "POST":
        nome = request.form["nome"].strip()
        telefone = request.form["telefone"].strip()
        material_principal = request.form["material_principal"].strip()
        tipo_fornecedor = request.form.get("tipo_fornecedor") or "Material"
        if tipo_fornecedor not in ("Material", "Mão de Obra"):
            tipo_fornecedor = "Material"

        conn.execute(
            """
            INSERT INTO fornecedores (usuario_id, nome, telefone, material_principal, tipo_fornecedor)
            VALUES (?, ?, ?, ?, ?)
        """,
            (uid, nome, telefone, material_principal, tipo_fornecedor),
        )
        conn.commit()
        conn.close()
        return redirect(url_for("fornecedores"))

    fornecedores_lista = conn.execute(
        "SELECT * FROM fornecedores WHERE usuario_id = ? ORDER BY id DESC", (uid,)
    ).fetchall()
    conn.close()

    mat_forn = [
        f for f in fornecedores_lista if (f["tipo_fornecedor"] or "Material") == "Material"
    ]
    mo_forn = [f for f in fornecedores_lista if f["tipo_fornecedor"] == "Mão de Obra"]

    def montar_linhas_forn(lista, vazio_msg):
        if not lista:
            return f'<tr><td colspan="4" style="text-align: center; color: #a0aec0;">{vazio_msg}</td></tr>'
        html = ""
        for f in lista:
            html += f"""
                <tr>
                    <td><strong style="color: #fff;">{f["nome"]}</strong></td>
                    <td>{f["telefone"] or "-"}</td>
                    <td>{f["material_principal"] or "-"}</td>
                    <td>
                        <a href="/editar_fornecedor/{f["id"]}" class="btn-detalhe" style="color: #d4af37;">Editar</a>
                        <a href="/excluir_fornecedor/{f["id"]}" class="btn-excluir" onclick="return confirm('Excluir fornecedor?')">Excluir</a>
                    </td>
                </tr>
            """
        return html

    rows_mat = montar_linhas_forn(mat_forn, "Nenhum fornecedor de material registado.")
    rows_mo = montar_linhas_forn(mo_forn, "Nenhum fornecedor de mão de obra registado.")

    body = f"""
        <a href="/" class="voltar">← Voltar para o Início</a>
        <div class="card">
            <div class="card-header-toggle" onclick="toggleSection(this, 'sec-form-forn')">
                <h2 style="margin:0;">Gerir Fornecedores</h2>
                <span class="toggle-arrow">▾ Fechar</span>
            </div>
            <div id="sec-form-forn" class="collapsible-body">
                <form method="post" style="margin-top: 15px; display: flex; gap: 12px; align-items: flex-end; flex-wrap: wrap;">
                    <div style="flex:1; min-width:180px;">
                        <label>Tipo:</label>
                        <select name="tipo_fornecedor" required>
                            <option value="Material">Fornecedor de Materiais</option>
                            <option value="Mão de Obra">Fornecedor de Mão de Obra</option>
                        </select>
                    </div>
                    <div style="flex:1; min-width:180px;">
                        <label>Nome:</label>
                        <input type="text" name="nome" autocomplete="off" required>
                    </div>
                    <div style="flex:1; min-width:160px;">
                        <label>Telefone:</label>
                        <input type="text" name="telefone" autocomplete="off">
                    </div>
                    <div style="flex:1; min-width:180px;">
                        <label>Especialidade / Material Principal:</label>
                        <input type="text" name="material_principal" autocomplete="off">
                    </div>
                    <div style="margin-bottom:12px;">
                        <button type="submit" class="btn">Registar</button>
                    </div>
                </form>
            </div>
        </div>

        <div class="card">
            <div class="card-header-toggle" onclick="toggleSection(this, 'sec-forn-materiais')">
                <h3 style="margin:0;">Fornecedores de Materiais</h3>
                <span class="toggle-arrow">▾ Fechar</span>
            </div>
            <div id="sec-forn-materiais" class="collapsible-body">
                <table>
                    <thead>
                        <tr>
                            <th>Nome</th>
                            <th>Telefone</th>
                            <th>Material Principal</th>
                            <th>Ações</th>
                        </tr>
                    </thead>
                    <tbody>{rows_mat}</tbody>
                </table>
            </div>
        </div>

        <div class="card">
            <div class="card-header-toggle" onclick="toggleSection(this, 'sec-forn-mo')">
                <h3 style="margin:0;">Fornecedores de Mão de Obra</h3>
                <span class="toggle-arrow">▾ Fechar</span>
            </div>
            <div id="sec-forn-mo" class="collapsible-body">
                <table>
                    <thead>
                        <tr>
                            <th>Nome</th>
                            <th>Telefone</th>
                            <th>Serviço / Especialidade</th>
                            <th>Ações</th>
                        </tr>
                    </thead>
                    <tbody>{rows_mo}</tbody>
                </table>
            </div>
        </div>
    """
    return render_layout(body, active_menu="fornecedores")


@app.route("/editar_fornecedor/<int:fornecedor_id>", methods=["GET", "POST"])
@login_required
def editar_fornecedor(fornecedor_id):
    uid = current_usuario_id()
    conn = get_db_connection()
    forn = conn.execute(
        "SELECT * FROM fornecedores WHERE id = ? AND usuario_id = ?",
        (fornecedor_id, uid),
    ).fetchone()
    if not forn:
        conn.close()
        return redirect(url_for("fornecedores"))

    if request.method == "POST":
        nome = (request.form.get("nome") or "").strip()
        telefone = (request.form.get("telefone") or "").strip()
        material_principal = (request.form.get("material_principal") or "").strip()
        tipo_fornecedor = request.form.get("tipo_fornecedor") or "Material"
        if tipo_fornecedor not in ("Material", "Mão de Obra"):
            tipo_fornecedor = "Material"

        if nome:
            conn.execute(
                """
                UPDATE fornecedores
                SET nome = ?, telefone = ?, material_principal = ?, tipo_fornecedor = ?
                WHERE id = ? AND usuario_id = ?
            """,
                (nome, telefone, material_principal, tipo_fornecedor, fornecedor_id, uid),
            )
            conn.commit()
        conn.close()
        return redirect(url_for("fornecedores"))

    conn.close()

    tipo_atual = forn["tipo_fornecedor"] or "Material"
    tipo_opts = f"""
        <option value="Material" {"selected" if tipo_atual == "Material" else ""}>Fornecedor de Materiais</option>
        <option value="Mão de Obra" {"selected" if tipo_atual == "Mão de Obra" else ""}>Fornecedor de Mão de Obra</option>
    """

    body = f"""
        <a href="/fornecedores" class="voltar">← Voltar para Fornecedores</a>
        <div class="card" style="max-width: 600px;">
            <h2 style="margin-top:0;">Editar Fornecedor</h2>
            <form method="post" style="margin-top: 15px;">
                <label>Tipo:</label>
                <select name="tipo_fornecedor" required>{tipo_opts}</select>

                <label>Nome:</label>
                <input type="text" name="nome" value="{forn["nome"]}" required>

                <label>Telefone:</label>
                <input type="text" name="telefone" value="{forn["telefone"] or ""}">

                <label>Especialidade / Material Principal:</label>
                <input type="text" name="material_principal" value="{forn["material_principal"] or ""}">

                <div style="display:flex; gap:10px; flex-wrap:wrap; margin-top:10px;">
                    <button type="submit" class="btn">Salvar Alterações</button>
                    <a href="/fornecedores" class="btn btn-secundario">Cancelar</a>
                </div>
            </form>
        </div>
    """
    return render_layout(body, active_menu="fornecedores")


@app.route("/excluir_fornecedor/<int:fornecedor_id>")
@login_required
def excluir_fornecedor(fornecedor_id):
    uid = current_usuario_id()
    conn = get_db_connection()
    forn = conn.execute(
        "SELECT id FROM fornecedores WHERE id = ? AND usuario_id = ?",
        (fornecedor_id, uid),
    ).fetchone()
    if forn:
        conn.execute(
            "UPDATE materiais_obra SET fornecedor_id = NULL WHERE fornecedor_id = ?",
            (fornecedor_id,),
        )
        conn.execute(
            "UPDATE pagamentos_mo_obra SET fornecedor_id = NULL WHERE fornecedor_id = ?",
            (fornecedor_id,),
        )
        conn.execute(
            "DELETE FROM fornecedores WHERE id = ? AND usuario_id = ?",
            (fornecedor_id, uid),
        )
        conn.commit()
    conn.close()
    return redirect(url_for("fornecedores"))


@app.route("/estoque", methods=["GET", "POST"])
@login_required
def estoque():
    uid = current_usuario_id()
    conn = get_db_connection()
    if request.method == "POST":
        descricao = request.form["descricao"].strip()
        fornecedor_id = request.form.get("fornecedor_id") or None
        if fornecedor_id:
            forn_ok = conn.execute(
                "SELECT id FROM fornecedores WHERE id = ? AND usuario_id = ?",
                (fornecedor_id, uid),
            ).fetchone()
            if not forn_ok:
                fornecedor_id = None

        unidade = request.form.get("unidade") or "un"
        qtd_comprada = parse_moeda(request.form.get("qtd_comprada"))
        valor_total_lote = parse_moeda(request.form.get("valor_total_lote"))

        if qtd_comprada > 0:
            conn.execute(
                """
                INSERT INTO estoque (usuario_id, descricao, fornecedor_id, unidade, qtd_comprada, valor_total_lote)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (uid, descricao, fornecedor_id, unidade, qtd_comprada, valor_total_lote),
            )
            conn.commit()
        conn.close()
        return redirect(url_for("estoque"))

    itens_estoque_cursor = conn.execute(
        """
        SELECT estoque.*, fornecedores.nome as fornecedor_nome
        FROM estoque
        LEFT JOIN fornecedores ON estoque.fornecedor_id = fornecedores.id AND fornecedores.usuario_id = estoque.usuario_id
        WHERE estoque.usuario_id = ?
        ORDER BY estoque.id DESC
    """,
        (uid,),
    ).fetchall()

    itens_estoque = []
    for item in itens_estoque_cursor:
        item_dict = dict(item)
        usado_row = conn.execute(
            "SELECT SUM(quantidade) as total FROM materiais_obra WHERE estoque_id = ?",
            (item["id"],),
        ).fetchone()
        usado = float(usado_row["total"] or 0.0)
        saldo_atual = float(item["qtd_comprada"] or 0.0) - usado

        if saldo_atual <= 0:
            conn.execute(
                "DELETE FROM estoque WHERE id = ? AND usuario_id = ?",
                (item["id"], uid),
            )
            conn.commit()
        else:
            item_dict["saldo_atual"] = saldo_atual
            itens_estoque.append(item_dict)

    fornecedores = conn.execute(
        "SELECT * FROM fornecedores WHERE usuario_id = ? AND (tipo_fornecedor = 'Material' OR tipo_fornecedor IS NULL) ORDER BY nome",
        (uid,),
    ).fetchall()
    conn.close()

    fornecedor_options = "".join(
        [f'<option value="{f["id"]}">{f["nome"]}</option>' for f in fornecedores]
    )

    rows = ""
    if itens_estoque:
        for item in itens_estoque:
            rows += f"""
                <tr>
                    <td><strong style="color: #fff;">{item["descricao"]}</strong></td>
                    <td>{item["fornecedor_nome"] or "Não informado"}</td>
                    <td>{item["unidade"]}</td>
                    <td style="color: #63b3ed; font-weight: bold;">{format_num_br(item["saldo_atual"], 2)} / {format_num_br(item["qtd_comprada"], 2)}</td>
                    <td>{format_brl(item["valor_total_lote"])}</td>
                    <td style="color: #48bb78; font-weight: bold;">{format_brl(item["valor_unitario"])} / {item["unidade"]}</td>
                    <td><a href="/excluir_estoque/{item["id"]}" class="btn-excluir" onclick="return confirm('Excluir lote?')">Excluir</a></td>
                </tr>
            """
    else:
        rows = '<tr><td colspan="7" style="text-align: center; color: #a0aec0;">Nenhum lote disponível em estoque.</td></tr>'

    body = f"""
        <a href="/" class="voltar">← Voltar para o Início</a>
        <div class="card">
            <div class="card-header-toggle" onclick="toggleSection(this, 'sec-form-estoque')">
                <h2 style="margin:0;">📦 Compras em Lote / Estoque Geral</h2>
                <span class="toggle-arrow">▾ Fechar</span>
            </div>
            <div id="sec-form-estoque" class="collapsible-body">
                <form method="post" style="margin-top: 15px; display: flex; gap: 12px; align-items: flex-end; flex-wrap: wrap;">
                    <div style="flex:1; min-width:180px;">
                        <label>Material:</label>
                        <input type="text" name="descricao" placeholder="Ex: Coluna" autocomplete="off" required>
                    </div>
                    <div style="flex:1; min-width:170px;">
                        <label>Fornecedor:</label>
                        <select name="fornecedor_id">
                            <option value="">Nenhum/Outro</option>
                            {fornecedor_options}
                        </select>
                    </div>
                    <div style="width:110px;">
                        <label>Unidade:</label>
                        <select name="unidade" required>
                            <option value="m">m</option>
                            <option value="m²">m²</option>
                            <option value="m³">m³</option>
                            <option value="un">un</option>
                            <option value="sc">sc</option>
                            <option value="kg">kg</option>
                        </select>
                    </div>
                    <div style="width:130px;">
                        <label>Qtd:</label>
                        <input type="text" name="qtd_comprada" placeholder="Ex: 50" required>
                    </div>
                    <div style="width:150px;">
                        <label>Total (R$):</label>
                        <input type="text" name="valor_total_lote" placeholder="Ex: 1500,00" required>
                    </div>
                    <div style="margin-bottom:12px;">
                        <button type="submit" class="btn">Cadastrar Lote</button>
                    </div>
                </form>
            </div>
        </div>

        <div class="card">
            <div class="card-header-toggle" onclick="toggleSection(this, 'sec-lotes-estoque')">
                <h3 style="margin:0;">Lotes em estoque (Saldo Disponível)</h3>
                <span class="toggle-arrow">▾ Fechar</span>
            </div>
            <div id="sec-lotes-estoque" class="collapsible-body">
                <table>
                    <thead>
                        <tr>
                            <th>Material</th>
                            <th>Fornecedor</th>
                            <th>Unidade</th>
                            <th>Saldo / Comprado</th>
                            <th>Valor Total</th>
                            <th>Custo Unitário</th>
                            <th>Ações</th>
                        </tr>
                    </thead>
                    <tbody>{rows}</tbody>
                </table>
            </div>
        </div>
    """
    return render_layout(body, active_menu="estoque")


@app.route("/excluir_estoque/<int:estoque_id>")
@login_required
def excluir_estoque(estoque_id):
    uid = current_usuario_id()
    conn = get_db_connection()
    conn.execute(
        "DELETE FROM estoque WHERE id = ? AND usuario_id = ?", (estoque_id, uid)
    )
    conn.commit()
    conn.close()
    return redirect(url_for("estoque"))


@app.route("/uploads/<filename>")
@login_required
def uploaded_file(filename):
    uid = current_usuario_id()
    safe_name = os.path.basename(filename)
    conn = get_db_connection()
    obra = conn.execute(
        "SELECT id FROM obras WHERE pdf_projeto = ? AND usuario_id = ?",
        (safe_name, uid),
    ).fetchone()
    conn.close()
    if not obra:
        return redirect(url_for("index"))
    return send_from_directory(app.config["UPLOAD_FOLDER"], safe_name)


@app.route("/editar_material/<int:material_id>", methods=["GET", "POST"])
@login_required
def editar_material(material_id):
    uid = current_usuario_id()
    conn = get_db_connection()
    item = conn.execute(
        """
        SELECT materiais_obra.*
        FROM materiais_obra
        JOIN obras ON materiais_obra.obra_id = obras.id
        WHERE materiais_obra.id = ? AND obras.usuario_id = ?
    """,
        (material_id, uid),
    ).fetchone()
    if not item:
        conn.close()
        return redirect(url_for("index"))

    obra_id = item["obra_id"]

    if request.method == "POST":
        descricao = request.form["descricao"].strip()
        categoria = request.form.get("categoria") or "Alvenaria"
        if categoria not in CATEGORIAS_OBRA:
            categoria = "Alvenaria"
        tipo_item = request.form.get("tipo_item") or "Material"
        if tipo_item not in ("Material", "Mão de Obra"):
            tipo_item = "Material"
        fornecedor_id = request.form.get("fornecedor_id") or None
        if fornecedor_id:
            forn_ok = conn.execute(
                "SELECT id FROM fornecedores WHERE id = ? AND usuario_id = ?",
                (fornecedor_id, uid),
            ).fetchone()
            if not forn_ok:
                fornecedor_id = None

        unidade = request.form.get("unidade") or "un"
        quantidade = parse_moeda(request.form.get("quantidade"))
        valor_unitario = parse_moeda(request.form.get("valor_unitario"))

        if item["estoque_id"]:
            est = conn.execute(
                "SELECT qtd_comprada FROM estoque WHERE id = ? AND usuario_id = ?",
                (item["estoque_id"], uid),
            ).fetchone()
            if est:
                usado_outros_row = conn.execute(
                    "SELECT SUM(quantidade) as total FROM materiais_obra WHERE estoque_id = ? AND id != ?",
                    (item["estoque_id"], material_id),
                ).fetchone()
                usado_outros = float(usado_outros_row["total"] or 0.0)
                max_permitido = float(est["qtd_comprada"] or 0.0) - usado_outros
                if quantidade > max_permitido:
                    conn.close()
                    return f"<script>alert('Erro: Quantidade maior que o saldo disponível no lote ({format_num_br(max_permitido, 2)}).'); window.history.back();</script>"

        conn.execute(
            """
            UPDATE materiais_obra
            SET descricao = ?, categoria = ?, tipo_item = ?, fornecedor_id = ?,
                unidade = ?, quantidade = ?, valor_unitario = ?
            WHERE id = ?
        """,
            (
                descricao,
                categoria,
                tipo_item,
                fornecedor_id,
                unidade,
                quantidade,
                valor_unitario,
                material_id,
            ),
        )
        conn.commit()
        conn.close()
        return redirect(url_for("detalhe_obra", obra_id=obra_id))

    fornecedores = conn.execute(
        "SELECT * FROM fornecedores WHERE usuario_id = ? ORDER BY nome", (uid,)
    ).fetchall()
    conn.close()

    cat_opts = "".join(
        [
            f'<option value="{c}" {"selected" if c == (item["categoria"] or "Alvenaria") else ""}>{c}</option>'
            for c in CATEGORIAS_OBRA
        ]
    )
    tipo_atual = item["tipo_item"] or "Material"
    tipo_opts = f"""
        <option value="Material" {"selected" if tipo_atual == "Material" else ""}>Material</option>
        <option value="Mão de Obra" {"selected" if tipo_atual == "Mão de Obra" else ""}>Mão de Obra</option>
    """
    forn_opts = '<option value="">Nenhum</option>' + "".join(
        [
            f'<option value="{f["id"]}" {"selected" if f["id"] == item["fornecedor_id"] else ""}>{f["nome"]}</option>'
            for f in fornecedores
        ]
    )

    body = f"""
        <a href="/obra/{obra_id}" class="voltar">← Voltar para a Obra</a>
        <div class="card" style="max-width: 600px;">
            <h2>Editar Material / Mão de Obra</h2>
            <form method="post" style="margin-top: 15px;">
                <label>Descrição:</label>
                <input type="text" name="descricao" value="{item["descricao"]}" required>

                <label>Tipo:</label>
                <select name="tipo_item">{tipo_opts}</select>

                <label>Categoria (Etapa):</label>
                <select name="categoria">{cat_opts}</select>

                <label>Fornecedor:</label>
                <select name="fornecedor_id">{forn_opts}</select>

                <label>Unidade:</label>
                <input type="text" name="unidade" value="{item["unidade"]}" required>

                <label>Quantidade:</label>
                <input type="text" name="quantidade" value="{format_num_br(item["quantidade"], 2)}" required>

                <label>Valor Unitário (R$):</label>
                <input type="text" name="valor_unitario" value="{format_num_br(item["valor_unitario"], 2)}" required>

                <div style="display:flex; gap:10px; flex-wrap:wrap; margin-top:10px;">
                    <button type="submit" class="btn">Salvar Alterações</button>
                    <a href="/obra/{obra_id}" class="btn btn-secundario">Cancelar</a>
                </div>
            </form>
        </div>
    """
    return render_layout(body, active_menu="resumo")


@app.route("/obra/<int:obra_id>", methods=["GET", "POST"])
@login_required
def detalhe_obra(obra_id):
    uid = current_usuario_id()
    conn = get_db_connection()

    dono_obra = conn.execute(
        "SELECT id FROM obras WHERE id = ? AND usuario_id = ?", (obra_id, uid)
    ).fetchone()
    if not dono_obra:
        conn.close()
        return redirect(url_for("index"))

    if request.method == "POST":
        if "atualizar_etapa" in request.form:
            etapa_id = request.form["etapa_id"]
            novo_perc = parse_moeda(request.form.get("percentual"))
            conn.execute(
                "UPDATE etapas_obra SET percentual = ? WHERE id = ? AND obra_id = ?",
                (novo_perc, etapa_id, obra_id),
            )
            conn.commit()
            conn.close()
            return redirect(url_for("detalhe_obra", obra_id=obra_id))

        if "salvar_valor_m2" in request.form:
            novo_valor_m2 = parse_moeda(request.form.get("valor_m2_usuario"))
            conn.execute(
                "UPDATE obras SET valor_m2_usuario = ? WHERE id = ? AND usuario_id = ?",
                (novo_valor_m2, obra_id, uid),
            )
            conn.commit()
            conn.close()
            return redirect(url_for("detalhe_obra", obra_id=obra_id))

        if "registrar_parcela_mo" in request.form:
            cat_mo = request.form.get("categoria_mo") or "Alvenaria"
            if cat_mo not in CATEGORIAS_OBRA:
                cat_mo = "Alvenaria"
            fornecedor_id_mo = request.form.get("fornecedor_id_mo") or None
            prof_nome = (request.form.get("profissional_nome") or "").strip()
            if fornecedor_id_mo:
                forn_row = conn.execute(
                    "SELECT id, nome FROM fornecedores WHERE id = ? AND usuario_id = ?",
                    (fornecedor_id_mo, uid),
                ).fetchone()
                if forn_row:
                    fornecedor_id_mo = forn_row["id"]
                    if not prof_nome:
                        prof_nome = forn_row["nome"]
                else:
                    fornecedor_id_mo = None

            valor_total_informado = parse_moeda(request.form.get("valor_total_contrato"))
            valor_parcela = parse_moeda(request.form.get("valor_parcela"))
            observacao = (request.form.get("observacao_mo") or "").strip()

            if prof_nome and valor_parcela > 0:
                existente_contrato = conn.execute(
                    """
                    SELECT valor_total_contrato
                    FROM pagamentos_mo_obra
                    WHERE obra_id = ? AND categoria = ? AND LOWER(profissional_nome) = LOWER(?)
                    ORDER BY id ASC LIMIT 1
                """,
                    (obra_id, cat_mo, prof_nome),
                ).fetchone()

                total_fixo = (
                    valor_total_informado
                    if valor_total_informado > 0
                    else (
                        float(existente_contrato["valor_total_contrato"] or 0.0)
                        if existente_contrato
                        else valor_parcela
                    )
                )

                if valor_total_informado > 0:
                    conn.execute(
                        """
                        UPDATE pagamentos_mo_obra
                        SET valor_total_contrato = ?
                        WHERE obra_id = ? AND categoria = ? AND LOWER(profissional_nome) = LOWER(?)
                    """,
                        (total_fixo, obra_id, cat_mo, prof_nome),
                    )

                conn.execute(
                    """
                    INSERT INTO pagamentos_mo_obra (
                        obra_id, fornecedor_id, categoria, profissional_nome,
                        valor_total_contrato, valor_parcela, observacao
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        obra_id,
                        fornecedor_id_mo,
                        cat_mo,
                        prof_nome,
                        total_fixo,
                        valor_parcela,
                        observacao,
                    ),
                )
                conn.commit()
            conn.close()
            return redirect(url_for("detalhe_obra", obra_id=obra_id))

        estoque_id = request.form.get("estoque_id")
        if estoque_id:
            item_est = conn.execute(
                "SELECT * FROM estoque WHERE id = ? AND usuario_id = ?",
                (estoque_id, uid),
            ).fetchone()
            if item_est:
                usado_row = conn.execute(
                    "SELECT SUM(quantidade) as total FROM materiais_obra WHERE estoque_id = ?",
                    (estoque_id,),
                ).fetchone()
                usado_real = float(usado_row["total"] or 0.0)
                saldo_disponivel = float(item_est["qtd_comprada"] or 0.0) - usado_real
                quantidade_desejada = parse_moeda(request.form.get("quantidade_lote"))
                categoria_lote = request.form.get("categoria_lote") or "Alvenaria"
                if categoria_lote not in CATEGORIAS_OBRA:
                    categoria_lote = "Alvenaria"

                if quantidade_desejada > saldo_disponivel:
                    conn.close()
                    return f"<script>alert('Erro: Quantidade solicitada maior que o saldo disponível em estoque ({format_num_br(saldo_disponivel, 2)} {item_est['unidade']})'); window.history.back();</script>"

                if quantidade_desejada > 0:
                    conn.execute(
                        """
                        INSERT INTO materiais_obra (
                            obra_id, fornecedor_id, estoque_id, categoria, tipo_item,
                            descricao, unidade, quantidade, valor_unitario
                        )
                        VALUES (?, ?, ?, ?, 'Material', ?, ?, ?, ?)
                    """,
                        (
                            obra_id,
                            item_est["fornecedor_id"],
                            estoque_id,
                            categoria_lote,
                            item_est["descricao"],
                            item_est["unidade"],
                            quantidade_desejada,
                            item_est["valor_unitario"],
                        ),
                    )
                    conn.commit()
        else:
            descricao = (request.form.get("descricao") or "").strip()
            categoria = request.form.get("categoria") or "Alvenaria"
            if categoria not in CATEGORIAS_OBRA:
                categoria = "Alvenaria"
            tipo_item = request.form.get("tipo_item") or "Material"
            if tipo_item not in ("Material", "Mão de Obra"):
                tipo_item = "Material"
            unidade = request.form.get("unidade") or "un"
            quantidade = parse_moeda(request.form.get("quantidade"))
            valor_unitario = parse_moeda(request.form.get("valor_unitario"))
            fornecedor_id = request.form.get("fornecedor_id") or None
            if fornecedor_id:
                forn_ok = conn.execute(
                    "SELECT id FROM fornecedores WHERE id = ? AND usuario_id = ?",
                    (fornecedor_id, uid),
                ).fetchone()
                if not forn_ok:
                    fornecedor_id = None

            if descricao and quantidade > 0:
                conn.execute(
                    """
                    INSERT INTO materiais_obra (
                        obra_id, fornecedor_id, estoque_id, categoria, tipo_item,
                        descricao, unidade, quantidade, valor_unitario
                    )
                    VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        obra_id,
                        fornecedor_id,
                        categoria,
                        tipo_item,
                        descricao,
                        unidade,
                        quantidade,
                        valor_unitario,
                    ),
                )
                conn.commit()

        conn.close()
        return redirect(url_for("detalhe_obra", obra_id=obra_id))

    obra = conn.execute(
        """
        SELECT obras.*, clientes.nome as cliente_nome, clientes.telefone as cliente_tel
        FROM obras
        LEFT JOIN clientes ON obras.cliente_id = clientes.id AND clientes.usuario_id = obras.usuario_id
        WHERE obras.id = ? AND obras.usuario_id = ?
    """,
        (obra_id, uid),
    ).fetchone()

    if not obra:
        conn.close()
        return redirect(url_for("index"))

    materiais = conn.execute(
        """
        SELECT materiais_obra.*, fornecedores.nome as fornecedor_nome
        FROM materiais_obra
        LEFT JOIN fornecedores ON materiais_obra.fornecedor_id = fornecedores.id AND fornecedores.usuario_id = ?
        WHERE materiais_obra.obra_id = ?
        ORDER BY materiais_obra.id DESC
    """,
        (uid, obra_id),
    ).fetchall()

    pagamentos_mo = conn.execute(
        "SELECT * FROM pagamentos_mo_obra WHERE obra_id = ? ORDER BY id ASC",
        (obra_id,),
    ).fetchall()

    etapas = conn.execute(
        "SELECT * FROM etapas_obra WHERE obra_id = ? ORDER BY id ASC", (obra_id,)
    ).fetchall()
    fornecedores = conn.execute(
        "SELECT * FROM fornecedores WHERE usuario_id = ? ORDER BY nome", (uid,)
    ).fetchall()

    itens_estoque_cursor = conn.execute(
        "SELECT * FROM estoque WHERE usuario_id = ?", (uid,)
    ).fetchall()
    itens_estoque = []
    for item in itens_estoque_cursor:
        usado_row = conn.execute(
            "SELECT SUM(quantidade) as total FROM materiais_obra WHERE estoque_id = ?",
            (item["id"],),
        ).fetchone()
        usado = float(usado_row["total"] or 0.0)
        saldo = float(item["qtd_comprada"] or 0.0) - usado
        if saldo > 0:
            item_dict = dict(item)
            item_dict["saldo"] = saldo
            itens_estoque.append(item_dict)

    conn.close()

    progresso_medio = (
        sum(float(e["percentual"] or 0.0) for e in etapas) / len(etapas)
        if etapas
        else 0.0
    )
    custo_materiais = sum(
        float(m["valor_total"] or 0.0) for m in materiais if m["valor_total"] is not None
    )
    custo_total_obra = custo_materiais + float(obra["valor_terreno"] or 0.0)
    receita_total = float(obra["valor_financiamento"] or 0.0) + float(
        obra["valor_entrada"] or 0.0
    )
    lucro_estimado = receita_total - custo_total_obra
    area_m2 = float(obra["metro_quadrado"] or 0.0)
    custo_por_m2 = (custo_total_obra / area_m2) if area_m2 > 0 else 0.0
    valor_m2_usuario = float(obra["valor_m2_usuario"] or 0.0)
    valor_total_m2_usuario = valor_m2_usuario * area_m2

    etapas_rows = ""
    for etapa in etapas:
        perc = float(etapa["percentual"] or 0.0)
        status_color = (
            "#48bb78" if perc >= 100 else ("#ed8936" if perc > 0 else "#a0aec0")
        )
        status_text = (
            "Concluído" if perc >= 100 else ("Em Andamento" if perc > 0 else "Não Iniciado")
        )
        etapas_rows += f"""
            <tr>
                <td>{etapa["nome_etapa"]}</td>
                <td>
                    <form method="post" style="display:flex; align-items:center; gap:6px; margin:0;">
                        <input type="hidden" name="atualizar_etapa" value="1">
                        <input type="hidden" name="etapa_id" value="{etapa["id"]}">
                        <input type="text" name="percentual" value="{format_num_br(perc, 1)}" style="width:75px; margin:0; padding:6px;"> %
                        <button type="submit" class="btn" style="padding:6px 10px; font-size:12px;">Atualizar</button>
                    </form>
                </td>
                <td><strong style="color: {status_color};">{status_text}</strong></td>
            </tr>
        """

    blocos_categorias_html = ""
    for idx_cat, cat_nome in enumerate(CATEGORIAS_OBRA):
        itens_cat = [
            m for m in materiais if (m["categoria"] or "Alvenaria") == cat_nome
        ]
        subtotal_cat = sum(float(m["valor_total"] or 0.0) for m in itens_cat)
        if itens_cat:
            rows_cat = ""
            for m in itens_cat:
                tipo_badge = (
                    '<span class="badge-tipo badge-mo">Mão de Obra</span>'
                    if m["tipo_item"] == "Mão de Obra"
                    else '<span class="badge-tipo badge-mat">Material</span>'
                )
                rows_cat += f"""
                    <tr>
                        <td><strong style="color: #fff;">{m["descricao"]}</strong></td>
                        <td>{tipo_badge}</td>
                        <td>{m["fornecedor_nome"] or "Não informado"}</td>
                        <td>{m["unidade"]}</td>
                        <td>{format_num_br(m["quantidade"], 2)}</td>
                        <td>{format_brl(m["valor_unitario"])}</td>
                        <td style="font-weight:bold; color:#fff;">{format_brl(m["valor_total"])}</td>
                        <td>
                            <a href="/editar_material/{m["id"]}" class="btn-detalhe" style="color:#d4af37;">Editar</a>
                            <a href="/excluir_material/{m["id"]}" class="btn-excluir" onclick="return confirm('Excluir item?')">Excluir</a>
                        </td>
                    </tr>
                """
        else:
            rows_cat = f'<tr><td colspan="8" style="text-align: center; color: #a0aec0;">Nenhum lançamento em {cat_nome}.</td></tr>'

        pag_cat = [
            p for p in pagamentos_mo if (p["categoria"] or "Alvenaria") == cat_nome
        ]
        total_pago_cat = sum(float(p["valor_parcela"] or 0.0) for p in pag_cat)
        contratos_cat = {}
        for p in pag_cat:
            chave = (p["profissional_nome"] or "").strip().lower()
            if chave not in contratos_cat:
                contratos_cat[chave] = {
                    "nome": p["profissional_nome"],
                    "total_contrato": float(p["valor_total_contrato"] or 0.0),
                    "pago": 0.0,
                }
            contratos_cat[chave]["pago"] += float(p["valor_parcela"] or 0.0)
            if float(p["valor_total_contrato"] or 0.0) > 0:
                contratos_cat[chave]["total_contrato"] = float(p["valor_total_contrato"])

        saldo_restante_cat = sum(
            max(0.0, c["total_contrato"] - c["pago"]) for c in contratos_cat.values()
        )

        if pag_cat:
            rows_mo_sub = ""
            acumulado_por_prof = {}
            for p in pag_cat:
                chave = (p["profissional_nome"] or "").strip().lower()
                total_fixo_item = float(p["valor_total_contrato"] or 0.0)
                parcela_item = float(p["valor_parcela"] or 0.0)
                acumulado_por_prof[chave] = acumulado_por_prof.get(chave, 0.0) + parcela_item
                restante_item = max(0.0, total_fixo_item - acumulado_por_prof[chave])
                restante_cor = "#48bb78" if restante_item == 0 else "#f6e05e"
                rows_mo_sub += f"""
                    <tr>
                        <td><strong style="color:#fff;">{p["profissional_nome"]}</strong></td>
                        <td>{p["observacao"] or "Parcela"}</td>
                        <td>{format_brl(total_fixo_item)}</td>
                        <td style="color:#63b3ed; font-weight:600;">{format_brl(parcela_item)}</td>
                        <td style="color:{restante_cor}; font-weight:700;">{format_brl(restante_item)}</td>
                        <td>
                            <a href="/excluir_parcela_mo/{p["id"]}" class="btn-excluir" onclick="return confirm('Excluir este registro de parcela?')">Excluir</a>
                        </td>
                    </tr>
                """
        else:
            rows_mo_sub = f'<tr><td colspan="6" style="text-align:center; color:#a0aec0;">Nenhum controle de mão de obra parcelada em {cat_nome}.</td></tr>'

        subtabela_mo_html = f"""
            <div style="margin-top:20px; padding-top:16px; border-top:1px dashed #2d3748;">
                <h4 style="margin:0 0 8px 0; color:#d4af37; font-size:14px;">
                    Subtabela — Controle de Mão de Obra ({cat_nome}) · Pago: {format_brl(total_pago_cat)} · Restante: {format_brl(saldo_restante_cat)}
                </h4>
                <form method="post" style="display:flex; gap:10px; align-items:flex-end; flex-wrap:wrap; margin-bottom:10px;">
                    <input type="hidden" name="registrar_parcela_mo" value="1">
                    <input type="hidden" name="categoria_mo" value="{cat_nome}">
                    <div style="flex:1; min-width:170px;">
                        <label>Profissional / M.O.:</label>
                        <input type="text" name="profissional_nome" placeholder="Ex: Fulano Pedreiro" required style="margin-bottom:0;">
                    </div>
                    <div style="width:160px;">
                        <label>Valor Total Acordado (R$):</label>
                        <input type="text" name="valor_total_contrato" placeholder="Ex: 30000,00" style="margin-bottom:0;">
                    </div>
                    <div style="width:150px;">
                        <label>Valor Parcela Paga (R$):</label>
                        <input type="text" name="valor_parcela" placeholder="Ex: 1000,00" required style="margin-bottom:0;">
                    </div>
                    <div style="flex:1; min-width:150px;">
                        <label>Ref. / Parcela:</label>
                        <input type="text" name="observacao_mo" placeholder="Ex: 1ª parcela" style="margin-bottom:0;">
                    </div>
                    <div>
                        <button type="submit" class="btn" style="padding:10px 14px; font-size:13px;">Lançar Parcela</button>
                    </div>
                </form>
                <table>
                    <thead>
                        <tr>
                            <th>Profissional</th>
                            <th>Parcela / Ref.</th>
                            <th>Valor Total (Fixo)</th>
                            <th>Parcela Paga</th>
                            <th>Saldo Restante</th>
                            <th>Ações</th>
                        </tr>
                    </thead>
                    <tbody>{rows_mo_sub}</tbody>
                </table>
            </div>
        """

        sec_id = f"sec-cat-{idx_cat}"
        blocos_categorias_html += f"""
            <div class="card">
                <div class="card-header-toggle" onclick="toggleSection(this, '{sec_id}')">
                    <h3 style="margin:0;">{cat_nome} <span style="color:#d4af37; font-size:14px;">— Subtotal: {format_brl(subtotal_cat)}</span></h3>
                    <span class="toggle-arrow">▾ Fechar</span>
                </div>
                <div id="{sec_id}" class="collapsible-body">
                    <table>
                        <thead>
                            <tr>
                                <th>Descrição</th>
                                <th>Tipo</th>
                                <th>Fornecedor</th>
                                <th>Unidade</th>
                                <th>Quantidade</th>
                                <th>Valor Unitário</th>
                                <th>Valor Total</th>
                                <th>Ações</th>
                            </tr>
                        </thead>
                        <tbody>{rows_cat}</tbody>
                    </table>
                    {subtabela_mo_html}
                </div>
            </div>
        """

    estoque_options = "".join(
        [
            f'<option value="{i["id"]}">{i["descricao"]} (Disp: {format_num_br(i["saldo"], 2)} {i["unidade"]} | {format_brl(i["valor_unitario"])})</option>'
            for i in itens_estoque
        ]
    )
    forn_options = "".join(
        [
            f'<option value="{f["id"]}">{f["nome"]} ({f["tipo_fornecedor"] or "Material"})</option>'
            for f in fornecedores
        ]
    )
    cat_select_options = "".join(
        [f'<option value="{c}">{c}</option>' for c in CATEGORIAS_OBRA]
    )

    if obra["pdf_projeto"]:
        pdf_proj = f'<a href="/uploads/{obra["pdf_projeto"]}" target="_blank" style="color: #d4af37; font-weight: bold;">Abrir PDF</a>'
    else:
        pdf_proj = "Nenhum PDF"

    lucro_cor = "#48bb78" if lucro_estimado >= 0 else "#fc8181"

    body = f"""
        <a href="/" class="voltar">← Voltar para o Início</a>
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; margin-bottom:20px;">
            <h1 style="margin:0;">Obra: {obra["nome_obra"]}</h1>
            <a href="/editar_obra/{obra["id"]}" class="btn btn-secundario">✏️ Editar Dados da Obra</a>
        </div>

        <div class="card">
            <div class="card-header-toggle" onclick="toggleSection(this, 'sec-resumo-obra')">
                <h3 style="margin:0;">Resumo Financeiro e Métricas</h3>
                <span class="toggle-arrow">▾ Fechar</span>
            </div>
            <div id="sec-resumo-obra" class="collapsible-body" style="margin-top:15px;">
                <p><strong>Cliente:</strong> {obra["cliente_nome"] or "-"} (Tel: {obra["cliente_tel"] or "-"})</p>
                <p><strong>Financiamento:</strong> {format_brl(obra["valor_financiamento"])} | <strong>Entrada:</strong> {format_brl(obra["valor_entrada"])}</p>
                <p><strong>Terreno:</strong> {format_brl(obra["valor_terreno"])} | <strong>Área:</strong> {format_num_br(area_m2, 2)} m²</p>
                <p><strong>Progresso Médio:</strong> <span style="color: #63b3ed; font-weight: bold;">{format_num_br(progresso_medio, 1)}%</span></p>
                <hr>
                <p><strong>Total Materiais / M.O.:</strong> {format_brl(custo_materiais)} | <strong>Custo Total:</strong> {format_brl(custo_total_obra)}</p>
                <p><strong>Custo por m²:</strong> {format_brl(custo_por_m2)} / m²</p>
                <p><strong>Lucro Estimado:</strong> <span style="color: {lucro_cor}; font-weight: bold;">{format_brl(lucro_estimado)}</span></p>
                <p><strong>Projeto PDF:</strong> {pdf_proj}</p>

                <hr>
                <form method="post" style="display:flex; gap:10px; align-items:flex-end; flex-wrap:wrap; margin-top:10px;">
                    <input type="hidden" name="salvar_valor_m2" value="1">
                    <div style="min-width:220px;">
                        <label><strong>Seu Valor por m² (R$/m²):</strong></label>
                        <input type="text" name="valor_m2_usuario" value="{format_num_br(valor_m2_usuario, 2)}" placeholder="Ex: 2800,00" style="margin-bottom:0;">
                    </div>
                    <button type="submit" class="btn">Salvar Valor m²</button>
                </form>
                <p style="margin-top:10px; color:#d4af37; font-size:13px;">
                    <strong>Valor Estimado pela Área ({format_num_br(area_m2, 2)} m² × {format_brl(valor_m2_usuario)}):</strong> {format_brl(valor_total_m2_usuario)}
                </p>
            </div>
        </div>

        <div class="card" style="border-left: 5px solid #d4af37;">
            <div class="card-header-toggle" onclick="toggleSection(this, 'sec-puxar-estoque')">
                <h3 style="margin:0;">📦 Puxar do Estoque / Lote Comprado</h3>
                <span class="toggle-arrow">▾ Fechar</span>
            </div>
            <div id="sec-puxar-estoque" class="collapsible-body">
                <form method="post" style="margin-top: 15px; display: flex; gap: 12px; align-items: flex-end; flex-wrap: wrap;">
                    <div style="flex:1; min-width:220px;">
                        <label>Lote:</label>
                        <select name="estoque_id" required>
                            <option value="">Selecione um lote...</option>
                            {estoque_options}
                        </select>
                    </div>
                    <div style="width:170px;">
                        <label>Categoria:</label>
                        <select name="categoria_lote">{cat_select_options}</select>
                    </div>
                    <div style="width:120px;">
                        <label>Qtd:</label>
                        <input type="text" name="quantidade_lote" placeholder="Ex: 10" required>
                    </div>
                    <div style="margin-bottom:12px;">
                        <button type="submit" class="btn">Alocar na Obra</button>
                    </div>
                </form>
            </div>
        </div>

        <div class="card">
            <div class="card-header-toggle" onclick="toggleSection(this, 'sec-compra-avulsa')">
                <h3 style="margin:0;">🛠️ Registar Material ou Mão de Obra</h3>
                <span class="toggle-arrow">▾ Fechar</span>
            </div>
            <div id="sec-compra-avulsa" class="collapsible-body">
                <form method="post" style="margin-top: 15px; display: flex; gap: 12px; align-items: flex-end; flex-wrap: wrap;">
                    <div style="flex:1; min-width:180px;">
                        <label>Descrição:</label>
                        <input type="text" name="descricao" required>
                    </div>
                    <div style="width:150px;">
                        <label>Tipo:</label>
                        <select name="tipo_item">
                            <option value="Material">Material</option>
                            <option value="Mão de Obra">Mão de Obra</option>
                        </select>
                    </div>
                    <div style="width:160px;">
                        <label>Categoria:</label>
                        <select name="categoria">{cat_select_options}</select>
                    </div>
                    <div style="min-width:170px;">
                        <label>Fornecedor:</label>
                        <select name="fornecedor_id">
                            <option value="">Nenhum</option>
                            {forn_options}
                        </select>
                    </div>
                    <div style="width:105px;">
                        <label>Unidade:</label>
                        <select name="unidade">
                            <option value="un">un</option>
                            <option value="m">m</option>
                            <option value="m²">m²</option>
                            <option value="m³">m³</option>
                            <option value="sc">sc</option>
                            <option value="kg">kg</option>
                            <option value="vb">vb (verba)</option>
                            <option value="diária">diária</option>
                        </select>
                    </div>
                    <div style="width:110px;">
                        <label>Qtd:</label>
                        <input type="text" name="quantidade" required>
                    </div>
                    <div style="width:130px;">
                        <label>Unitário (R$):</label>
                        <input type="text" name="valor_unitario" required>
                    </div>
                    <div style="margin-bottom:12px;">
                        <button type="submit" class="btn">Adicionar</button>
                    </div>
                </form>
            </div>
        </div>

        {blocos_categorias_html}

        <div class="card">
            <div class="card-header-toggle" onclick="toggleSection(this, 'sec-cronograma')">
                <h3 style="margin:0;">Cronograma e Medição (Caixa Econômica Federal)</h3>
                <span class="toggle-arrow">▾ Fechar</span>
            </div>
            <div id="sec-cronograma" class="collapsible-body">
                <table>
                    <thead>
                        <tr>
                            <th>Etapa da Obra</th>
                            <th>% Executado</th>
                            <th>Status</th>
                        </tr>
                    </thead>
                    <tbody>{etapas_rows}</tbody>
                </table>
            </div>
        </div>
    """
    return render_layout(body, active_menu="resumo")


@app.route("/excluir_parcela_mo/<int:parcela_id>")
@login_required
def excluir_parcela_mo(parcela_id):
    uid = current_usuario_id()
    conn = get_db_connection()
    row = conn.execute(
        """
        SELECT pagamentos_mo_obra.obra_id
        FROM pagamentos_mo_obra
        JOIN obras ON pagamentos_mo_obra.obra_id = obras.id
        WHERE pagamentos_mo_obra.id = ? AND obras.usuario_id = ?
    """,
        (parcela_id, uid),
    ).fetchone()
    if row:
        obra_id = row["obra_id"]
        conn.execute("DELETE FROM pagamentos_mo_obra WHERE id = ?", (parcela_id,))
        conn.commit()
        conn.close()
        return redirect(url_for("detalhe_obra", obra_id=obra_id))
    conn.close()
    return redirect(url_for("index"))


@app.route("/excluir_material/<int:material_id>")
@login_required
def excluir_material(material_id):
    uid = current_usuario_id()
    conn = get_db_connection()
    material = conn.execute(
        """
        SELECT materiais_obra.obra_id
        FROM materiais_obra
        JOIN obras ON materiais_obra.obra_id = obras.id
        WHERE materiais_obra.id = ? AND obras.usuario_id = ?
    """,
        (material_id, uid),
    ).fetchone()
    if material:
        obra_id = material["obra_id"]
        conn.execute("DELETE FROM materiais_obra WHERE id = ?", (material_id,))
        conn.commit()
        conn.close()
        return redirect(url_for("detalhe_obra", obra_id=obra_id))
    conn.close()
    return redirect(url_for("index"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)