from flask import Flask, render_template_string, request, redirect, url_for, send_from_directory
import sqlite3
import os

app = Flask(__name__)

UPLOAD_FOLDER = 'static/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def get_db_connection():
    conn = sqlite3.connect('orcamento_obras.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            telefone TEXT,
            endereco TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS fornecedores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            telefone TEXT,
            material_principal TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS estoque (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            descricao TEXT NOT NULL,
            fornecedor_id INTEGER,
            unidade TEXT NOT NULL DEFAULT 'un',
            qtd_comprada REAL NOT NULL,
            valor_total_lote REAL NOT NULL,
            valor_unitario REAL GENERATED ALWAYS AS (valor_total_lote / NULLIF(qtd_comprada, 0)) STORED,
            FOREIGN KEY (fornecedor_id) REFERENCES fornecedores (id)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS obras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id INTEGER,
            nome_obra TEXT NOT NULL,
            valor_financiamento REAL DEFAULT 0,
            valor_entrada REAL DEFAULT 0,
            valor_terreno REAL DEFAULT 0,
            metro_quadrado REAL DEFAULT 0,
            pdf_projeto TEXT,
            FOREIGN KEY (cliente_id) REFERENCES clientes (id)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS etapas_obra (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            obra_id INTEGER,
            nome_etapa TEXT NOT NULL,
            percentual REAL DEFAULT 0,
            FOREIGN KEY (obra_id) REFERENCES obras (id)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS materiais_obra (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            obra_id INTEGER,
            fornecedor_id INTEGER,
            estoque_id INTEGER,
            descricao TEXT NOT NULL,
            unidade TEXT NOT NULL DEFAULT 'un',
            quantidade REAL NOT NULL,
            valor_unitario REAL NOT NULL,
            valor_total REAL GENERATED ALWAYS AS (quantidade * valor_unitario) STORED,
            FOREIGN KEY (obra_id) REFERENCES obras (id),
            FOREIGN KEY (fornecedor_id) REFERENCES fornecedores (id),
            FOREIGN KEY (estoque_id) REFERENCES estoque (id)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

ETAPAS_PADRAO = [
    "Serviços Preliminares e Gerais",
    "Infraestrutura",
    "Supraestrutura",
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
    "Outros Serviços (Muro, portão, calçada)"
]

BASE_CSS = '''
    * { box-sizing: border-box; }
    body {
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        margin: 0;
        background-color: #0f1115;
        color: #e2e8f0;
        display: flex;
        min-height: 100vh;
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
        padding: 9px 18px;
        text-decoration: none;
        border-radius: 6px;
        display: inline-block;
        font-weight: 600;
        border: none;
        cursor: pointer;
        font-size: 13px;
        transition: all 0.2s;
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
    }
    .btn-excluir:hover {
        background: rgba(231, 76, 60, 0.3);
    }
    input, select {
        padding: 9px 12px;
        border: 1px solid #2d3748;
        border-radius: 6px;
        font-size: 13px;
        outline: none;
        background: #111318;
        color: #ffffff;
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
        font-size: 13px;
    }
    a.voltar:hover {
        text-decoration: underline;
    }
    .progress-bar-container {
        background: #2d3748;
        border-radius: 4px;
        overflow: hidden;
        width: 90px;
        height: 8px;
        display: inline-block;
        vertical-align: middle;
        margin-right: 8px;
    }
    .progress-bar-fill {
        background: #d4af37;
        height: 100%;
    }
    hr {
        border: 0;
        border-top: 1px solid #2d3748;
        margin: 15px 0;
    }

    /* RESPONSIVIDADE PARA TELEMÓVEIS / CELULARES */
    @media (max-width: 768px) {
        .sidebar {
            width: 70px;
            overflow: hidden;
        }
        .sidebar-header h2, .sidebar-menu li a span, .sidebar-footer a span {
            display: none;
        }
        .main-wrapper {
            margin-left: 70px;
            width: calc(100% - 70px);
        }
        .metrics-container {
            grid-template-columns: 1fr;
        }
        .content-area {
            padding: 15px;
        }
        .top-navbar {
            padding: 0 15px;
        }
    }
'''

def render_layout(body_content, active_menu='resumo'):
    menu_active = {
        'resumo': '', 'clientes': '', 'obra': '', 'fornecedores': '', 'estoque': ''
    }
    menu_active[active_menu] = 'active'
    
    return render_template_string(f'''
        <!DOCTYPE html>
        <html lang="pt">
        <head>
            <meta charset="UTF-8">
            <title>Construtora Quality - Painel de Engenharia</title>
            <style>'''+ BASE_CSS +'''</style>
        </head>
        <body>
            <div class="sidebar">
                <div class="sidebar-header">
                    <img src="/static/logo.jpg" alt="Logo">
                    <h2>CONSTRUTORA QUALITY</h2>
                </div>
                <ul class="sidebar-menu">
                    <li><a href="/" class="{menu_active['resumo']}">📊 Resumo Geral</a></li>
                    <li><a href="/novo_cliente" class="{menu_active['clientes']}">👤 Clientes</a></li>
                    <li><a href="/nova_obra" class="{menu_active['obra']}">🏗️ Registar Obra</a></li>
                    <li><a href="/fornecedores" class="{menu_active['fornecedores']}">🤝 Fornecedores</a></li>
                    <li><a href="/estoque" class="{menu_active['estoque']}">📦 Estoque / Lotes</a></li>
                </ul>
                <div class="sidebar-footer">
                    <a href="/">🚪 Sair do Sistema</a>
                </div>
            </div>
            
            <div class="main-wrapper">
                <div class="top-navbar">
                    <span style="font-weight: 600; font-size: 14px; letter-spacing: 0.5px; color: #d4af37;">PAINEL DE GESTÃO DE OBRAS</span>
                    <div class="user-info">
                        <span>Construtora Quality ▾</span>
                    </div>
                </div>
                <div class="content-area">
                    '''+ body_content +'''
                </div>
            </div>
        </body>
        </html>
    ''')

@app.route('/')
def index():
    conn = get_db_connection()
    obras_cursor = conn.execute('''
        SELECT obras.*, clientes.nome as cliente_nome 
        FROM obras 
        LEFT JOIN clientes ON obras.cliente_id = clientes.id
    ''').fetchall()
    
    obras = []
    total_custo_geral = 0
    for o in obras_cursor:
        obra_dict = dict(o)
        etapas = conn.execute('SELECT percentual FROM etapas_obra WHERE obra_id = ?', (o['id'],)).fetchall()
        if etapas:
            media_progresso = sum(e['percentual'] for e in etapas) / len(etapas)
        else:
            media_progresso = 0.0
        obra_dict['progresso_medio'] = media_progresso
        
        materiais = conn.execute('SELECT SUM(valor_total) as total FROM materiais_obra WHERE obra_id = ?', (o['id'],)).fetchone()['total'] or 0
        custo_obra = materiais + (o['valor_terreno'] or 0)
        total_custo_geral += custo_obra
        
        obras.append(obra_dict)
        
    total_obras = len(obras)
    progresso_geral_medio = (sum(o['progresso_medio'] for o in obras) / total_obras) if total_obras > 0 else 0.0
    conn.close()
    
    body = f'''
        <div class="top-bar-title">
            <div>
                <h1>Resumo Geral das Obras</h1>
                <p>Acompanhamento financeiro, físico e orçamental das construções.</p>
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
                <div class="value" style="color: #48bb78;">R$ {total_custo_geral:,.2f}</div>
            </div>
            <div class="metric-card">
                <h4>Progresso Médio Geral</h4>
                <div class="value" style="color: #63b3ed;">{progresso_geral_medio:.1f}%</div>
            </div>
        </div>

        <div class="card">
            <h3 style="margin-top: 0; margin-bottom: 15px; font-size: 16px;">Lista de Obras em Andamento</h3>
            <table>
                <tr>
                    <th>ID</th>
                    <th>Nome da Obra</th>
                    <th>Cliente</th>
                    <th>Financiamento</th>
                    <th>Área (m²)</th>
                    <th>% Executado</th>
                    <th>Projeto PDF</th>
                    <th>Ações</th>
                </tr>
    '''
    
    if obras:
        for obra in obras:
            pdf_link = f'<a href="/uploads/{obra["pdf_projeto"]}" target="_blank" style="color: #d4af37; font-weight: bold;">Ver PDF</a>' if obra['pdf_projeto'] else '<span style="color: #718096;">Sem Projeto</span>'
            body += f'''
                <tr>
                    <td>{obra["id"]:02d}</td>
                    <td><strong style="color: #ffffff;">{obra["nome_obra"]}</strong></td>
                    <td>{obra["cliente_nome"] or "Sem Cliente"}</td>
                    <td>R$ {obra["valor_financiamento"]:,.2f}</td>
                    <td>{obra["metro_quadrado"]} m²</td>
                    <td>
                        <div class="progress-bar-container"><div class="progress-bar-fill" style="width: {obra["progresso_medio"]}%;"></div></div>
                        <strong style="color: #63b3ed;">{obra["progresso_medio"]:.1f}%</strong>
                    </td>
                    <td>{pdf_link}</td>
                    <td>
                        <a href="/obra/{obra["id"]}" class="btn-detalhe">Orçamento</a>
                        <a href="/excluir_obra/{obra["id"]}" class="btn-excluir" onclick="return confirm('Deseja excluir esta obra?')">Excluir</a>
                    </td>
                </tr>
            '''
    else:
        body += '<tr><td colspan="8" style="text-align: center; color: #a0aec0;">Nenhuma obra registada até o momento.</td></tr>'

    body += '''
            </table>
        </div>
    '''
    return render_layout(body, active_menu='resumo')

@app.route('/novo_cliente', methods=('GET', 'POST'))
def novo_cliente():
    if request.method == 'POST':
        nome = request.form['nome']
        telefone = request.form['telefone']
        endereco = request.form['endereco']
        
        conn = get_db_connection()
        conn.execute('INSERT INTO clientes (nome, telefone, endereco) VALUES (?, ?, ?)',
                     (nome, telefone, endereco))
        conn.commit()
        conn.close()
        return redirect(url_for('index'))
        
    body = '''
        <a href="/" class="voltar">← Voltar para o Início</a>
        <div class="card" style="max-width: 550px;">
            <h2>Registar Novo Cliente</h2>
            <form method="post" style="margin-top: 15px;">
                <p>Nome:<br><input type="text" name="nome" autocomplete="off" required style="width: 100%;"></p>
                <p>Telefone:<br><input type="text" name="telefone" autocomplete="off" style="width: 100%;"></p>
                <p>Endereço:<br><input type="text" name="endereco" autocomplete="off" style="width: 100%;"></p>
                <button type="submit" class="btn" style="width: 100%; margin-top: 10px;">Salvar Cliente</button>
            </form>
        </div>
    '''
    return render_layout(body, active_menu='clientes')

@app.route('/nova_obra', methods=('GET', 'POST'))
def nova_obra():
    conn = get_db_connection()
    if request.method == 'POST':
        cliente_id = request.form['cliente_id']
        nome_obra = request.form['nome_obra']
        valor_financiamento = float(request.form['valor_financiamento'] or 0.0)
        valor_entrada = float(request.form['valor_entrada'] or 0.0)
        valor_terreno = float(request.form['valor_terreno'] or 0.0)
        metro_quadrado = float(request.form['metro_quadrado'] or 0.0)
        
        pdf_filename = None
        if 'pdf_projeto' in request.files:
            file = request.files['pdf_projeto']
            if file and file.filename.endswith('.pdf'):
                pdf_filename = file.filename
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], pdf_filename))
        
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO obras (cliente_id, nome_obra, valor_financiamento, valor_entrada, valor_terreno, metro_quadrado, pdf_projeto)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (cliente_id, nome_obra, valor_financiamento, valor_entrada, valor_terreno, metro_quadrado, pdf_filename))
        obra_id = cursor.lastrowid
        
        for etapa in ETAPAS_PADRAO:
            conn.execute('INSERT INTO etapas_obra (obra_id, nome_etapa, percentual) VALUES (?, ?, 0)', (obra_id, etapa))
            
        conn.commit()
        conn.close()
        return redirect(url_for('index'))
        
    clientes = conn.execute('SELECT * FROM clientes').fetchall()
    conn.close()
    
    options = "".join([f'<option value="{c["id"]}">{c["nome"]}</option>' for c in clientes])
    
    body = f'''
        <a href="/" class="voltar">← Voltar para o Início</a>
        <div class="card" style="max-width: 650px;">
            <h2>Registar Nova Obra</h2>
            <form method="post" enctype="multipart/form-data" style="margin-top: 15px;">
                <p>Cliente:<br>
                <select name="cliente_id" required style="width: 100%;">
                    <option value="">Selecione um cliente</option>
                    {options}
                </select></p>
                <p>Nome da Obra:<br><input type="text" name="nome_obra" autocomplete="off" required style="width: 100%;"></p>
                <p>Valor Financiamento (R$):<br><input type="number" step="0.01" name="valor_financiamento" autocomplete="off" style="width: 100%;"></p>
                <p>Valor Entrada (R$):<br><input type="number" step="0.01" name="valor_entrada" autocomplete="off" style="width: 100%;"></p>
                <p>Valor do Terreno (R$):<br><input type="number" step="0.01" name="valor_terreno" autocomplete="off" style="width: 100%;"></p>
                <p>Área da Casa (m²):<br><input type="number" step="0.01" name="metro_quadrado" autocomplete="off" style="width: 100%;"></p>
                <p>Projeto (PDF):<br><input type="file" name="pdf_projeto" accept=".pdf" style="width: 100%;"></p>
                <button type="submit" class="btn" style="width: 100%; margin-top: 10px;">Salvar Obra</button>
            </form>
        </div>
    '''
    return render_layout(body, active_menu='obra')

@app.route('/excluir_obra/<int:obra_id>')
def excluir_obra(obra_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM materiais_obra WHERE obra_id = ?', (obra_id,))
    conn.execute('DELETE FROM etapas_obra WHERE obra_id = ?', (obra_id,))
    conn.execute('DELETE FROM obras WHERE id = ?', (obra_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/fornecedores', methods=('GET', 'POST'))
def fornecedores():
    conn = get_db_connection()
    if request.method == 'POST':
        nome = request.form['nome']
        telefone = request.form['telefone']
        material_principal = request.form['material_principal']
        
        conn.execute('INSERT INTO fornecedores (nome, telefone, material_principal) VALUES (?, ?, ?)',
                     (nome, telefone, material_principal))
        conn.commit()
        return redirect(url_for('fornecedores'))
        
    fornecedores_lista = conn.execute('SELECT * FROM fornecedores').fetchall()
    conn.close()
    
    rows = ""
    if fornecedores_lista:
        for f in fornecedores_lista:
            rows += f'''
                <tr>
                    <td><strong style="color: #fff;">{f["nome"]}</strong></td>
                    <td>{f["telefone"]}</td>
                    <td>{f["material_principal"]}</td>
                    <td><a href="/excluir_fornecedor/{f["id"]}" class="btn-excluir" onclick="return confirm('Excluir fornecedor?')">Excluir</a></td>
                </tr>
            '''
    else:
        rows = '<tr><td colspan="4" style="text-align: center; color: #a0aec0;">Nenhum fornecedor registado.</td></tr>'

    body = f'''
        <a href="/" class="voltar">← Voltar para o Início</a>
        <div class="card">
            <h2>Gerir Fornecedores</h2>
            <form method="post" style="margin-top: 15px; display: flex; gap: 10px; align-items: flex-end;">
                <div>Nome:<br><input type="text" name="nome" autocomplete="off" required></div>
                <div>Telefone:<br><input type="text" name="telefone" autocomplete="off"></div>
                <div>Material Principal:<br><input type="text" name="material_principal" autocomplete="off"></div>
                <button type="submit" class="btn">Registar</button>
            </form>
        </div>
        <div class="card">
            <h3>Lista de Fornecedores</h3>
            <table>
                <tr><th>Nome</th><th>Telefone</th><th>Material Principal</th><th>Ações</th></tr>
                {rows}
            </table>
        </div>
    '''
    return render_layout(body, active_menu='fornecedores')

@app.route('/excluir_fornecedor/<int:fornecedor_id>')
def excluir_fornecedor(fornecedor_id):
    conn = get_db_connection()
    conn.execute('UPDATE materiais_obra SET fornecedor_id = NULL WHERE fornecedor_id = ?', (fornecedor_id,))
    conn.execute('DELETE FROM fornecedores WHERE id = ?', (fornecedor_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('fornecedores'))

@app.route('/estoque', methods=('GET', 'POST'))
def estoque():
    conn = get_db_connection()
    if request.method == 'POST':
        descricao = request.form['descricao']
        fornecedor_id = request.form['fornecedor_id'] or None
        unidade = request.form['unidade'] or 'un'
        qtd_comprada = float(request.form['qtd_comprada'] or 0)
        valor_total_lote = float(request.form['valor_total_lote'] or 0)
        
        conn.execute('''
            INSERT INTO estoque (descricao, fornecedor_id, unidade, qtd_comprada, valor_total_lote)
            VALUES (?, ?, ?, ?, ?)
        ''', (descricao, fornecedor_id, unidade, qtd_comprada, valor_total_lote))
        conn.commit()
        return redirect(url_for('estoque'))
        
    itens_estoque_cursor = conn.execute('''
        SELECT estoque.*, fornecedores.nome as fornecedor_nome 
        FROM estoque 
        LEFT JOIN fornecedores ON estoque.fornecedor_id = fornecedores.id
    ''').fetchall()
    
    itens_estoque = []
    for item in itens_estoque_cursor:
        item_dict = dict(item)
        usado = conn.execute('SELECT SUM(quantidade) as total FROM materiais_obra WHERE estoque_id = ?', (item['id'],)).fetchone()['total'] or 0
        saldo_atual = item['qtd_comprada'] - usado
        
        if saldo_atual <= 0:
            conn.execute('DELETE FROM estoque WHERE id = ?', (item['id'],))
            conn.commit()
        else:
            item_dict['saldo_atual'] = saldo_atual
            itens_estoque.append(item_dict)

    fornecedores = conn.execute('SELECT * FROM fornecedores').fetchall()
    conn.close()
    
    fornecedor_options = "".join([f'<option value="{f["id"]}">{f["nome"]}</option>' for f in fornecedores])
    
    rows = ""
    if itens_estoque:
        for item in itens_estoque:
            rows += f'''
                <tr>
                    <td><strong style="color: #fff;">{item["descricao"]}</strong></td>
                    <td>{item["fornecedor_nome"] or "Não informado"}</td>
                    <td>{item["unidade"]}</td>
                    <td style="color: #63b3ed; font-weight: bold;">{item["saldo_atual"]} / {item["qtd_comprada"]}</td>
                    <td>R$ {item["valor_total_lote"]:,.2f}</td>
                    <td style="color: #48bb78; font-weight: bold;">R$ {item["valor_unitario"]:,.2f} / {item["unidade"]}</td>
                    <td><a href="/excluir_estoque/{item["id"]}" class="btn-excluir" onclick="return confirm('Excluir lote?')">Excluir</a></td>
                </tr>
            '''
    else:
        rows = '<tr><td colspan="7" style="text-align: center; color: #a0aec0;">Nenhum lote disponível em estoque.</td></tr>'

    body = f'''
        <a href="/" class="voltar">← Voltar para o Início</a>
        <div class="card">
            <h2>📦 Compras em Lote / Estoque Geral</h2>
            <form method="post" style="margin-top: 15px; display: flex; gap: 10px; align-items: flex-end; flex-wrap: wrap;">
                <div>Material:<br><input type="text" name="descricao" placeholder="Ex: Coluna" autocomplete="off" required></div>
                <div>Fornecedor:<br>
                    <select name="fornecedor_id">
                        <option value="">Nenhum/Outro</option>
                        {fornecedor_options}
                    </select>
                </div>
                <div>Unidade:<br>
                    <select name="unidade" required>
                        <option value="m">m</option><option value="m²">m²</option><option value="m³">m³</option>
                        <option value="un">un</option><option value="sc">sc</option><option value="kg">kg</option>
                    </select>
                </div>
                <div>Qtd:<br><input type="number" step="0.01" name="qtd_comprada" required style="width: 80px;"></div>
                <div>Total (R$):<br><input type="number" step="0.01" name="valor_total_lote" required style="width: 100px;"></div>
                <button type="submit" class="btn">Cadastrar Lote</button>
            </form>
        </div>
        <div class="card">
            <h3>Lotes em estoque (Saldo Disponível)</h3>
            <table>
                <tr><th>Material</th><th>Fornecedor</th><th>Unidade</th><th>Saldo / Comprado</th><th>Valor Total</th><th>Custo Unitário</th><th>Ações</th></tr>
                {rows}
            </table>
        </div>
    '''
    return render_layout(body, active_menu='estoque')

@app.route('/excluir_estoque/<int:estoque_id>')
def excluir_estoque(estoque_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM estoque WHERE id = ?', (estoque_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('estoque'))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/obra/<int:obra_id>', methods=('GET', 'POST'))
def detalhe_obra(obra_id):
    conn = get_db_connection()
    
    if request.method == 'POST':
        if 'atualizar_etapa' in request.form:
            etapa_id = request.form['etapa_id']
            novo_perc = float(request.form['percentual'] or 0)
            conn.execute('UPDATE etapas_obra SET percentual = ? WHERE id = ?', (novo_perc, etapa_id))
            conn.commit()
            conn.close()
            return redirect(url_for('detalhe_obra', obra_id=obra_id))

        estoque_id = request.form.get('estoque_id')
        if estoque_id:
            item_est = conn.execute('SELECT * FROM estoque WHERE id = ?', (estoque_id,)).fetchone()
            if item_est:
                usado = conn.execute('SELECT SUM(quantidade) as total FROM materiais_obra WHERE estoque_id = ?', (item['id'],)).fetchone()['total'] or 0 if 'item' in locals() else 0
                # recalculando saldo correto
                usado_real = conn.execute('SELECT SUM(quantidade) as total FROM materiais_obra WHERE estoque_id = ?', (estoque_id,)).fetchone()['total'] or 0
                saldo_disponivel = item_est['qtd_comprada'] - usado_real
                quantidade_desejada = float(request.form['quantidade_lote'] or 0)
                
                if quantidade_desejada > saldo_disponivel:
                    conn.close()
                    return f"<script>alert('Erro: Quantidade solicitada maior que o saldo disponível em estoque ({saldo_disponivel} {item_est.unidade})!'); window.history.back();</script>"
                
                conn.execute('''
                    INSERT INTO materiais_obra (obra_id, fornecedor_id, estoque_id, descricao, unidade, quantidade, valor_unitario)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (obra_id, item_est['fornecedor_id'], estoque_id, item_est['descricao'], item_est['unidade'], quantidade_desejada, item_est['valor_unitario']))
                conn.commit()
        else:
            descricao = request.form['descricao']
            unidade = request.form['unidade'] or 'un'
            quantidade = float(request.form['quantidade'] or 0)
            valor_unitario = float(request.form['valor_unitario'] or 0)
            fornecedor_id = request.form['fornecedor_id'] or None
            
            if quantidade > 0:
                conn.execute('''
                    INSERT INTO materiais_obra (obra_id, fornecedor_id, descricao, unidade, quantidade, valor_unitario)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (obra_id, fornecedor_id, descricao, unidade, quantidade, valor_unitario))
                conn.commit()
                
        conn.close()
        return redirect(url_for('detalhe_obra', obra_id=obra_id))
        
    obra = conn.execute('''
        SELECT obras.*, clientes.nome as cliente_nome, clientes.telefone as cliente_tel 
        FROM obras 
        LEFT JOIN clientes ON obras.cliente_id = clientes.id 
        WHERE obras.id = ?
    ''', (obra_id,)).fetchone()
    
    materiais = conn.execute('''
        SELECT materiais_obra.*, fornecedores.nome as fornecedor_nome 
        FROM materiais_obra 
        LEFT JOIN fornecedores ON materiais_obra.fornecedor_id = fornecedores.id 
        WHERE materiais_obra.obra_id = ?
    ''', (obra_id,)).fetchall()
    
    etapas = conn.execute('SELECT * FROM etapas_obra WHERE obra_id = ?', (obra_id,)).fetchall()
    fornecedores = conn.execute('SELECT * FROM fornecedores').fetchall()
    
    itens_estoque_cursor = conn.execute('SELECT * FROM estoque').fetchall()
    itens_estoque = []
    for item in itens_estoque_cursor:
        usado = conn.execute('SELECT SUM(quantidade) as total FROM materiais_obra WHERE estoque_id = ?', (item['id'],)).fetchone()['total'] or 0
        saldo = item['qtd_comprada'] - usado
        if saldo > 0:
            item_dict = dict(item)
            item_dict['saldo'] = saldo
            itens_estoque.append(item_dict)
    
    progresso_medio = (sum(e['percentual'] for e in etapas) / len(etapas)) if etapas else 0.0
    custo_materiais = sum(m['valor_total'] for m in materiais if m['valor_total'] is not None)
    custo_total_obra = custo_materiais + (obra['valor_terreno'] or 0.0)
    receita_total = (obra['valor_financiamento'] or 0.0) + (obra['valor_entrada'] or 0.0)
    lucro_estimado = receita_total - custo_total_obra
    custo_por_m2 = (custo_total_obra / obra['metro_quadrado']) if (obra['metro_quadrado'] or 0) > 0 else 0.0
    
    conn.close()
    
    etapas_rows = ""
    for etapa in etapas:
        status_color = "#48bb78" if etapa['percentual'] == 100 else ("#ed8936" if etapa['percentual'] > 0 else "#a0aec0")
        status_text = "Concluído" if etapa['percentual'] == 100 else ("Em Andamento" if etapa['percentual'] > 0 else "Não Iniciado")
        etapas_rows += f'''
            <tr>
                <td>{etapa["nome_etapa"]}</td>
                <td>
                    <form method="post" style="display:inline;">
                        <input type="hidden" name="atualizar_etapa" value="1">
                        <input type="hidden" name="etapa_id" value="{etapa["id"]}">
                        <input type="number" step="0.1" min="0" max="100" name="percentual" value="{etapa["percentual"]}" style="width: 70px;"> %
                        <button type="submit" class="btn" style="padding: 4px 8px; font-size: 11px;">Atualizar</button>
                    </form>
                </td>
                <td><strong style="color: {status_color};">{status_text}</strong></td>
            </tr>
        '''

    materiais_rows = ""
    if materiais:
        for m in materiais:
            materiais_rows += f'''
                <tr>
                    <td><strong style="color: #fff;">{m["descricao"]}</strong></td>
                    <td>{m["fornecedor_nome"] or "Não informado"}</td>
                    <td>{m["unidade"]}</td>
                    <td>{m["quantidade"]}</td>
                    <td>R$ {m["valor_unitario"]:,.2f}</td>
                    <td>R$ {m["valor_total"]:,.2f}</td>
                    <td><a href="/excluir_material/{m["id"]}" class="btn-excluir" onclick="return confirm('Excluir item?')">Excluir</a></td>
                </tr>
            '''
    else:
        materiais_rows = '<tr><td colspan="7" style="text-align: center; color: #a0aec0;">Nenhum material registado.</td></tr>'

    estoque_options = "".join([f'<option value="{i["id"]}">{i["descricao"]} (Disp: {i["saldo"]} {i["unidade"]} | R$ {i["valor_unitario"]:,.2f})</option>' for i in itens_estoque])
    forn_options = "".join([f'<option value="{f["id"]}">{f["nome"]}</option>' for f in fornecedores])
    pdf_proj = f'<a href="/uploads/{obra["pdf_projeto"]}" target="_blank" style="color: #d4af37; font-weight: bold;">Abrir PDF</a>' if obra['pdf_projeto'] else 'Nenhum PDF'

    body = f'''
        <a href="/" class="voltar">← Voltar para o Início</a>
        <h1>Obra: {obra["nome_obra"]}</h1>
        
        <div class="card">
            <h3>Resumo Financeiro e Métricas</h3>
            <p><strong>Cliente:</strong> {obra["cliente_nome"]} (Tel: {obra["cliente_tel"]})</p>
            <p><strong>Financiamento:</strong> R$ {obra["valor_financiamento"]:,.2f} | <strong>Entrada:</strong> R$ {obra["valor_entrada"]:,.2f}</p>
            <p><strong>Terreno:</strong> R$ {obra["valor_terreno"]:,.2f} | <strong>Área:</strong> {obra["metro_quadrado"]} m²</p>
            <p><strong>Progresso Médio:</strong> <span style="color: #63b3ed; font-weight: bold;">{progresso_medio:.1f}%</span></p>
            <hr>
            <p><strong>Total Materiais:</strong> R$ {custo_materiais:,.2f} | <strong>Custo Total:</strong> R$ {custo_total_obra:,.2f}</p>
            <p><strong>Custo por m²:</strong> R$ {custo_por_m2:,.2f} / m²</p>
            <p><strong>Lucro Estimado:</strong> <span style="color: {"#48bb78" if lucro_estimado >= 0 else "#fc8181"}; font-weight: bold;">R$ {lucro_estimado:,.2f}</span></p>
            <p><strong>Projeto PDF:</strong> {pdf_proj}</p>
        </div>

        <div class="card">
            <h3>Cronograma e Medição (Caixa Econômica Federal)</h3>
            <table>
                <tr><th>Etapa da Obra</th><th>% Executado</th><th>Status</th></tr>
                {etapas_rows}
            </table>
        </div>

        <div class="card" style="border-left: 5px solid #d4af37;">
            <h3>📦 Puxar do Estoque / Lote Comprado</h3>
            <form method="post" style="margin-top: 10px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap;">
                <select name="estoque_id" required style="width: 300px;">
                    <option value="">Selecione um lote...</option>
                    {estoque_options}
                </select>
                Qtd: <input type="number" step="0.01" name="quantidade_lote" required style="width: 80px;">
                <button type="submit" class="btn">Alocar na Obra</button>
            </form>
        </div>

        <div class="card">
            <h3>🛒 Registar Compra Avulsa / Direta</h3>
            <form method="post" style="margin-top: 10px; display: flex; gap: 10px; align-items: flex-end; flex-wrap: wrap;">
                <div>Descrição:<br><input type="text" name="descricao" required></div>
                <div>Fornecedor:<br><select name="fornecedor_id"><option value="">Nenhum</option>{forn_options}</select></div>
                <div>Unidade:<br><select name="unidade"><option value="un">un</option><option value="m">m</option><option value="m²">m²</option><option value="m³">m³</option><option value="sc">sc</option><option value="kg">kg</option></select></div>
                <div>Qtd:<br><input type="number" step="0.01" name="quantidade" required style="width: 70px;"></div>
                <div>Unitário (R$):<br><input type="number" step="0.01" name="valor_unitario" required style="width: 90px;"></div>
                <button type="submit" class="btn">Adicionar</button>
            </form>
        </div>

        <div class="card">
            <h3>Lista Detalhada de Materiais Alocados</h3>
            <table>
                <tr><th>Descrição</th><th>Fornecedor</th><th>Unidade</th><th>Quantidade</th><th>Valor Unitário</th><th>Valor Total</th><th>Ações</th></tr>
                {materiais_rows}
            </table>
        </div>
    '''
    return render_layout(body, active_menu='obra')

@app.route('/excluir_material/<int:material_id>')
def excluir_material(material_id):
    conn = get_db_connection()
    material = conn.execute('SELECT obra_id FROM materiais_obra WHERE id = ?', (material_id,)).fetchone()
    obra_id = material['obra_id'] if material else 1
    conn.execute('DELETE FROM materiais_obra WHERE id = ?', (material_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('detalhe_obra', obra_id=obra_id))

import os

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)