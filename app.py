import random
import string
import streamlit as st
from datetime import datetime, timedelta
from supabase import create_client, Client

# 1. Configuração da página
st.set_page_config(
    page_title="F4 Connect",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------
# CONEXÃO SUPABASE
# ---------------------------------------------------------
SUPABASE_URL = "https://dmucssgskmhpqdkyovwc.supabase.co"
SUPABASE_KEY = "sb_publishable_sfUWEI0jRY36Hh1iRGeDEA_6MaBTPIy"

@st.cache_resource
def init_supabase():
    try:
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception:
        return None

supabase = init_supabase()

# Dicionário de Usuários Administradores
USUARIOS_ADMIN = {
    "felipe": "1234",
    "colega": "1234"
}

OPCOES_STATUS = [
    "Aguardando atendimento",
    "Em análise",
    "Em atendimento",
    "Concluído",
    "Cancelado",
    "Encerrado pelo solicitante"
]

def gerar_protocolo():
    letras_numeros = string.ascii_uppercase + string.digits
    codigo = "".join(random.choices(letras_numeros, k=6))
    return f"#F4-{codigo}"

def salvar_chamado_supabase(nome, email, empresa, ferramenta, assunto, descricao, severidade):
    protocolo = gerar_protocolo()
    dados = {
        "protocolo": protocolo,
        "nome_solicitante": nome,
        "email_solicitante": email,
        "empresa": empresa,
        "ferramenta": ferramenta,
        "assunto": assunto,
        "descricao": descricao,
        "severidade": severidade, # <--- NOVO CAMPO
        "status": "Aguardando atendimento"
    }
    if supabase:
        supabase.table("chamados").insert(dados).execute()
    return protocolo

def listar_chamados():
    if supabase:
        res = supabase.table("chamados").select("*").order("id", desc=True).execute()
        return res.data
    return []

def atualizar_status_chamado(protocolo, novo_status):
    if supabase:
        supabase.table("chamados").update({"status": novo_status}).eq("protocolo", protocolo).execute()

# ---------------------------------------------------------
# IMAGENS & SESSÃO
# ---------------------------------------------------------
# As imagens agora são servidas como arquivo estático pelo próprio Streamlit,
# em vez de embutidas em base64 no HTML. Isso exige:
#   1. Os arquivos dentro de uma pasta "static/" na raiz do projeto (já é o caso).
#   2. Um arquivo .streamlit/config.toml com:
#        [server]
#        enableStaticServing = true
robo_src = "https://github.com/felipecamboim-bite/f4-connect/releases/download/v1.0/roboanimado__semfundo.gif"
fundo_src = "https://github.com/felipecamboim-bite/f4-connect/releases/download/v1.0/fundo_animacao.png"

if "opcao_menu" not in st.session_state:
    st.session_state["opcao_menu"] = "inicio"

if "etapa_abertura" not in st.session_state:
    st.session_state["etapa_abertura"] = 1

if "ultimo_protocolo" not in st.session_state:
    st.session_state["ultimo_protocolo"] = None

if "usuario_logado" not in st.session_state:
    st.session_state["usuario_logado"] = None

if "temp_nome" not in st.session_state:
    st.session_state["temp_nome"] = ""
if "temp_empresa" not in st.session_state:
    st.session_state["temp_empresa"] = "Selecione..."

# ---------------------------------------------------------
# CSS DA INTERFACE & CONTAINERS DA TABELA ADMIN
# ---------------------------------------------------------
st.markdown(
    f"""
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&display=swap" rel="stylesheet">
    
    <style>
        .stApp {{
            background-image: url("{fundo_src}") !important;
            background-size: cover !important;
            background-position: center !important;
            background-repeat: no-repeat !important;
            background-attachment: fixed !important;
        }}

        header[data-testid="stHeader"] {{
            background-color: transparent !important;
            background: transparent !important;
        }}

        /* SIDEBAR COMPACTA */
        section[data-testid="stSidebar"] {{
            width: 280px !important;
            background-color: rgba(10, 25, 47, 0.75) !important;
            backdrop-filter: blur(8px) !important;
            border-right: 1px solid rgba(0, 183, 255, 0.3) !important;
        }}

        section[data-testid="stSidebar"] .stMarkdown, 
        section[data-testid="stSidebar"] label {{
            color: #FFFFFF !important;
            font-family: 'Inter', sans-serif !important;
        }}

        section[data-testid="stSidebar"] .stButton > button {{
            width: 100% !important;
            margin-bottom: 8px !important;
        }}

        .main .block-container {{
            padding-top: 1.5rem !important;
            padding-bottom: 2rem !important;
            padding-left: 3rem !important;
            padding-right: 3rem !important;
            max-width: 100% !important;
        }}

        @media (max-width: 768px) {{
            .main .block-container {{
                padding-left: 1.25rem !important;
                padding-right: 1.25rem !important;
                padding-top: 1rem !important;
            }}
        }}

        @media (max-width: 480px) {{
            .main .block-container {{
                padding-left: 0.85rem !important;
                padding-right: 0.85rem !important;
            }}
        }}

        .titulo-topo {{
            text-align: center;
            font-family: 'Inter', sans-serif !important;
            font-size: clamp(24px, 6vw, 46px);
            font-weight: 900;
            color: #FFFFFF !important;
            text-transform: uppercase;
            letter-spacing: clamp(1px, 0.6vw, 5px);
            margin-bottom: clamp(16px, 3vw, 30px);
            text-shadow: 0px 4px 12px rgba(0, 0, 0, 0.7);
        }}

        /* ROBÔ ENCOSTADO NO LADO DIREITO DA SUA COLUNA (MAIS PRÓXIMO DOS TEXTOS) */
        .robo-box {{
            display: flex !important;
            justify-content: flex-end !important;
            align-items: center !important;
            width: 100% !important;
            margin-top: 10px;
        }}

        .robo-box img {{
            width: 100% !important;
            max-width: 580px !important;
            height: auto !important;
            filter: drop-shadow(0px 12px 22px rgba(0,0,0,0.5));
        }}

        /* O deslocamento extra só faz sentido em telas largas, lado a lado com o menu.
           Em telas estreitas o Streamlit empilha as colunas e isso "jogaria" o robô pra fora. */
        @media (min-width: 1000px) {{
            .robo-box img {{
                transform: translateX(120px) !important;
            }}
        }}

        /* Colunas empilham abaixo de 640px (comportamento nativo do Streamlit).
           Nessa faixa o robô fica centralizado em vez de colado à direita. */
        @media (max-width: 640px) {{
            .robo-box {{
                justify-content: center !important;
            }}
        }}

        /* ALINHAMENTO CENTRALIZADO DAS COLUNAS */
        [data-testid="stColumns"] {{
            transform: none !important;
            align-items: center !important;
            overflow: visible !important;
        }}

        [data-testid="stColumn"] {{
            overflow: visible !important;
        }}

        /* O espaçamento extra no topo é só para empurrar o bloco robô + menu para
           baixo — escopado para não empurrar também cada linha da tabela de chamados */
        [data-testid="stHorizontalBlock"]:has(.robo-box) {{
            margin-top: 50px !important;
        }}

        /* CARD DE TÍTULO/FALA EXPANDIDO */
        .fala-titulo-card {{
            width: 100% !important;
            max-width: 460px !important;
            font-size: clamp(17px, 3.2vw, 22px);
            font-weight: 800;
            color: #FFFFFF !important;
            font-family: 'Inter', sans-serif !important;
            background-color: rgba(10, 25, 47, 0.6) !important;
            border: 1px solid rgba(0, 183, 255, 0.35);
            border-radius: 14px;
            padding: 16px clamp(16px, 4vw, 22px);
            box-shadow: 0 10px 24px rgba(0, 0, 0, 0.35);
            text-align: center !important;
            margin-bottom: 22px;
            backdrop-filter: blur(6px);
        }}

        /* BOTÕES AMPLIADOS E ESPAÇADOS */
        .stButton > button {{
            width: 100% !important;
            max-width: 460px !important;
            background-color: rgba(10, 25, 47, 0.55) !important;
            color: #FFFFFF !important;
            border: 1px solid rgba(0, 183, 255, 0.35) !important;
            border-radius: 14px !important;
            padding: 16px 20px !important;
            transition: all 0.2s ease-in-out;
            box-shadow: 0 8px 20px rgba(0, 0, 0, 0.25);
            margin-bottom: 16px !important;
            backdrop-filter: blur(6px);
        }}

        .stButton > button p {{
            font-size: clamp(16px, 2.6vw, 19px) !important;
            font-weight: 700 !important;
            font-family: 'Inter', sans-serif !important;
            color: #FFFFFF !important;
            margin: 0 !important;
        }}

        .stButton > button:hover {{
            background-color: rgba(0, 122, 255, 0.7) !important;
            border-color: #00d4ff !important;
            transform: translateY(-3px) !important;
            box-shadow: 0 10px 25px rgba(0, 212, 255, 0.35);
        }}

        .stTextInput label, .stSelectbox label, .stTextArea label {{
            font-size: 15px !important;
            font-weight: 600 !important;
            color: #FFFFFF !important;
            font-family: 'Inter', sans-serif !important;
        }}

        .stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb="select"] {{
            font-size: 16px !important; /* 16px trava o zoom automático do Safari/iOS ao tocar no campo */
            border-radius: 8px !important;
            background-color: rgba(10, 25, 47, 0.7) !important;
            color: #FFFFFF !important;
            border: 1px solid rgba(0, 183, 255, 0.3) !important;
        }}

        .card-sucesso {{
            width: 100% !important;
            max-width: 460px !important;
            background-color: rgba(6, 78, 59, 0.75) !important;
            border: 1px solid #10b981;
            border-radius: 14px;
            padding: 18px;
            color: #ffffff;
            font-family: 'Inter', sans-serif;
            text-align: center;
            margin-bottom: 20px;
        }}

        /* ========================================================= */
        /* ESTILOS DA TABELA ADMINISTRATIVA (CONTAINERS DE CONTEÚDO) */
        /* ========================================================= */

        .header-box {{
            background-color: rgba(10, 25, 47, 0.85);
            border: 1px solid rgba(0, 183, 255, 0.4);
            border-radius: 8px;
            padding: 10px 4px;
            text-align: center;
            font-family: 'Inter', sans-serif;
            font-weight: 800;
            font-size: 12px;
            color: #00d4ff;
            text-transform: uppercase;
            letter-spacing: 1px;
            box-shadow: 0 4px 10px rgba(0, 0, 0, 0.4);
            backdrop-filter: blur(6px);
            white-space: nowrap;
            overflow: hidden;
        }}

        .chamado-card-container {{
            background-color: rgba(10, 25, 47, 0.55);
            border: 1px solid rgba(0, 183, 255, 0.25);
            border-radius: 12px;
            padding: 12px 16px;
            margin-bottom: 10px;
            backdrop-filter: blur(8px);
            box-shadow: 0 6px 16px rgba(0,0,0,0.3);
            transition: all 0.2s ease-in-out;
        }}

        .chamado-card-container:hover {{
            border-color: rgba(0, 212, 255, 0.6);
            background-color: rgba(10, 25, 47, 0.75);
            box-shadow: 0 8px 20px rgba(0, 212, 255, 0.15);
        }}

        .celula-texto {{
            color: #FFFFFF;
            font-family: 'Inter', sans-serif;
            font-size: 14px;
            font-weight: 500;
            word-wrap: break-word;
            padding-top: 4px;
        }}

        .celula-protocolo {{
            color: #38bdf8;
            font-family: 'Inter', sans-serif;
            font-size: 14px;
            font-weight: 800;
        }}

        .badge-status {{
            background-color: rgba(0, 183, 255, 0.2);
            border: 1px solid #00d4ff;
            color: #00d4ff;
            border-radius: 20px;
            padding: 6px 12px;
            font-size: 13px;
            font-weight: 700;
            text-align: center;
            display: inline-block;
            font-family: 'Inter', sans-serif;
        }}

        /* ========================================================= */
        /* TABELAS DE 8 COLUNAS (PAINEL ADMIN + CONSULTA) EM TELAS    */
        /* ESTREITAS: viram uma lista de cards rotulados em vez de    */
        /* colunas espremidas.                                       */
        /* ========================================================= */

        .mobile-label {{
            display: none;
            color: #00d4ff;
            font-weight: 800;
            margin-right: 4px;
        }}

        @media (max-width: 1000px) {{
            /* a linha de cabeçalho (Protocolo | Solicitante | ...) fica redundante
               quando cada campo já mostra o próprio rótulo — some com ela */
            [data-testid="stHorizontalBlock"]:has(.header-box) {{
                display: none !important;
            }}

            /* linhas de dado: de colunas lado a lado para uma pilha vertical */
            [data-testid="stHorizontalBlock"]:has(.celula-protocolo) {{
                flex-direction: column !important;
                gap: 2px !important;
            }}

            [data-testid="stHorizontalBlock"]:has(.celula-protocolo) [data-testid="stColumn"] {{
                width: 100% !important;
                min-width: 100% !important;
                flex: 1 1 100% !important;
            }}

            .mobile-label {{
                display: inline;
            }}

            /* rótulo "Status" acima do seletor, já que ele não tem texto próprio
               (a versão somente-leitura da consulta já ganhou seu próprio <span> no Python) */
            [data-testid="stHorizontalBlock"]:has(.celula-protocolo) [data-testid="stColumn"]:last-child:has([data-baseweb="select"])::before {{
                content: "Status";
                display: block;
                color: #00d4ff;
                font-weight: 800;
                font-size: 11px;
                text-transform: uppercase;
                letter-spacing: 1px;
                margin: 6px 0 2px 0;
            }}
        }}
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------
# SIDEBAR (LOGIN ADMIN)
# ---------------------------------------------------------
with st.sidebar:
    st.markdown("### 🔐 Área Administrativa")

    if not st.session_state["usuario_logado"]:
        usuario = st.text_input("Usuário", key="user_admin")
        senha = st.text_input("Senha", type="password", key="pass_admin")

        if st.button("🔑 Entrar"):
            if usuario in USUARIOS_ADMIN and USUARIOS_ADMIN[usuario] == senha:
                st.session_state["usuario_logado"] = usuario
                st.success(f"Bem-vindo, {usuario}!")
                st.rerun()
            else:
                st.error("Usuário ou senha incorretos.")
    else:
        st.success(f"👋 Logado como: **{st.session_state['usuario_logado']}**")
        if st.button("🚪 Sair (Logout)"):
            st.session_state["usuario_logado"] = None
            st.rerun()

# ---------------------------------------------------------
# INTERFACE PRINCIPAL
# ---------------------------------------------------------
st.markdown(
    '<div class="titulo-topo">F4 Connect</div>',
    unsafe_allow_html=True,
)

# ------------------ VISÃO ADMIN (TABELA COM CARDS) ------------------
@st.fragment
def painel_admin():
    st.markdown("## 📊 Painel de Controle - Central de Chamados")

    chamados = listar_chamados()
    if not chamados:
        st.info("Nenhum chamado cadastrado até o momento.")
        return

    # 1. BLOCOS DE TITULOS/CABEÇALHO
    col_widths = [1.1, 1.2, 1.6, 1.1, 1.2, 1.1, 1.3, 1.8, 1.5]
    headers = ["Protocolo", "Solicitante", "E-mail", "Empresa", "Ferramenta", "Severidade", "Assunto", "Descrição", "Status"]

    cols_head = st.columns(col_widths)
    for col, h in zip(cols_head, headers):
        col.markdown(f'<div class="header-box">{h}</div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # 2. LINHAS EM FORMATO DE CARDS
    for c in chamados:
        with st.container():
            st.markdown('<div class="chamado-card-container">', unsafe_allow_html=True)

            c_proto, c_nome, c_mail, c_emp, c_ferr, c_sev, c_ass, c_desc, c_stat = st.columns(col_widths)

            c_proto.markdown(f'<div class="celula-protocolo"><span class="mobile-label">Protocolo:</span>{c.get("protocolo", "-")}</div>', unsafe_allow_html=True)
            c_nome.markdown(f'<div class="celula-texto"><span class="mobile-label">Solicitante:</span>{c.get("nome_solicitante", "-")}</div>', unsafe_allow_html=True)
            c_mail.markdown(f'<div class="celula-texto"><span class="mobile-label">E-mail:</span>{c.get("email_solicitante", "-")}</div>', unsafe_allow_html=True)
            c_emp.markdown(f'<div class="celula-texto"><span class="mobile-label">Empresa:</span>{c.get("empresa", "-")}</div>', unsafe_allow_html=True)
            c_ferr.markdown(f'<div class="celula-texto"><span class="mobile-label">Ferramenta:</span>{c.get("ferramenta", "-")}</div>', unsafe_allow_html=True)
            c_sev.markdown(f'<div class="celula-texto"><span class="mobile-label">Severidade:</span>{c.get("severidade") or "-"}</div>', unsafe_allow_html=True)
            c_ass.markdown(f'<div class="celula-texto"><span class="mobile-label">Assunto:</span>{c.get("assunto", "-")}</div>', unsafe_allow_html=True)
            c_desc.markdown(f'<div class="celula-texto"><span class="mobile-label">Descrição:</span>{c.get("descricao", "-")}</div>', unsafe_allow_html=True)

            idx_atual = OPCOES_STATUS.index(c['status']) if c['status'] in OPCOES_STATUS else 0
            novo_status = c_stat.selectbox(
                "Status",
                OPCOES_STATUS,
                index=idx_atual,
                key=f"status_{c['protocolo']}",
                label_visibility="collapsed"
            )

            if novo_status != c['status']:
                atualizar_status_chamado(c['protocolo'], novo_status)
                st.toast(f"Status do {c['protocolo']} atualizado para: {novo_status}")
                # rerun com escopo "fragment": atualiza só este painel,
                # sem re-executar o app inteiro (login, CSS, imagens etc.)
                st.rerun(scope="fragment")

            st.markdown('</div>', unsafe_allow_html=True)


if st.session_state["usuario_logado"]:
    painel_admin()

# ------------------ VISÃO PÚBLICA (SOLICITANTE) ------------------
else:
    col_robo, col_balao = st.columns([0.85, 1.6])
    with col_robo:
        st.markdown(
            f'<div class="robo-box"><img src="{robo_src}"></div>',
            unsafe_allow_html=True,
        )

    with col_balao:
        if st.session_state["opcao_menu"] == "inicio":
            st.session_state["ultimo_protocolo"] = None
            st.session_state["etapa_abertura"] = 1
            st.markdown(
                '<div class="fala-titulo-card">💬 Olá! Como posso te ajudar hoje?</div>',
                unsafe_allow_html=True,
            )

            if st.button("📝 Abrir um novo chamado"):
                st.session_state["opcao_menu"] = "abrir"
                st.rerun()

            if st.button("🔍 Acompanhar meu chamado"):
                st.session_state["opcao_menu"] = "acompanhar"
                st.rerun()

            if st.button("⭐ Avaliar um atendimento"):
                st.session_state["opcao_menu"] = "avaliar"
                st.rerun()

        elif st.session_state["opcao_menu"] == "abrir":
            if st.session_state["etapa_abertura"] == 1:
                st.markdown(
                    '<div class="fala-titulo-card">👤 Identificação Inicial:</div>',
                    unsafe_allow_html=True,
                )

                if st.session_state["ultimo_protocolo"]:
                    st.markdown(
                        f"""
                        <div class="card-sucesso">
                            ✅ <b>Chamado registrado com sucesso!</b><br>
                            Seu Protocolo: <b style="font-size: 20px; color: #34d399;">{st.session_state['ultimo_protocolo']}</b>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    st.session_state["ultimo_protocolo"] = None

                empresa = st.selectbox(
                    "Qual empresa você faz parte?",
                    [
                        "Selecione...",
                        "Clicklog transportes",
                        "Frete autônomo",
                        "Outra"
                    ],
                    index=0
                )

                nome = st.text_input(
                    "Digite seu Nome e Sobrenome",
                    value=st.session_state["temp_nome"],
                    placeholder="Ex: João Silva"
                )

                if st.button("Avançar →"):
                    nome_limpo = nome.strip()
                    partes_nome = nome_limpo.split()

                    if empresa == "Selecione...":
                        st.warning("⚠️ Selecione a empresa da qual você faz parte.")
                    elif len(partes_nome) < 2:
                        st.warning("⚠️ Digite seu nome completo (no mínimo Nome e Sobrenome).")
                    else:
                        st.session_state["temp_empresa"] = empresa
                        st.session_state["temp_nome"] = nome_limpo
                        st.session_state["etapa_abertura"] = 2
                        st.rerun()

                if st.button("← Voltar ao Menu"):
                    st.session_state["opcao_menu"] = "inicio"
                    st.rerun()

            elif st.session_state["etapa_abertura"] == 2:
                st.markdown(
                    '<div class="fala-titulo-card">📝 Detalhes do Chamado:</div>',
                    unsafe_allow_html=True,
                )

                email = st.text_input("Seu E-mail", placeholder="exemplo@empresa.com")
                ferramenta = st.selectbox(
                    "Escolha a ferramenta que necessita de ajuda",
                    [
                        "Selecione...",
                        "Escalasoft",
                        "SSW",
                        "E-mail",
                        "Drive",
                        "Frete autônomo",
                        "Dashboards (BI)",
                        "Roteirizador",
                        "Outro",
                    ],
                )

                # --- CAMPO DE SEVERIDADE ---
                severidade = st.selectbox(
                    "Nível de Severidade / Urgência do Chamado",
                    [
                        "Selecione...",
                        "🟢 Baixa",
                        "🟡 Média",
                        "🟠 Alta",
                        "🔴 Crítica"
                    ]
                )

                assunto = st.text_input("Assunto do chamado")
                descricao = st.text_area("Descrição detalhada do problema", placeholder="Conte-nos o que está acontecendo...")

                if st.button("🚀 Enviar Chamado"):
                    if not email or "@" not in email:
                        st.warning("⚠️ Digite um e-mail válido.")
                    elif ferramenta == "Selecione...":
                        st.warning("⚠️ Selecione a ferramenta.")
                    elif severidade == "Selecione...":
                        st.warning("⚠️ Selecione a severidade do chamado.")
                    elif not assunto.strip():
                        st.warning("⚠️ Informe o assunto.")
                    elif not descricao.strip():
                        st.warning("⚠️ Descreva detalhadamente o problema.")
                    else:
                        protocolo = salvar_chamado_supabase(
                            st.session_state["temp_nome"],
                            email,
                            st.session_state["temp_empresa"],
                            ferramenta,
                            assunto,
                            descricao,
                            severidade # <--- PASSANDO A SEVERIDADE
                        )
                        st.session_state["ultimo_protocolo"] = protocolo
                        st.session_state["etapa_abertura"] = 1
                        st.session_state["temp_nome"] = ""
                        st.session_state["temp_empresa"] = "Selecione..."
                        st.rerun()

                if st.button("← Voltar Etapa"):
                    st.session_state["etapa_abertura"] = 1
                    st.rerun()

        elif st.session_state["opcao_menu"] == "acompanhar":
            st.markdown(
                '<div class="fala-titulo-card">🔍 Consulte seu chamado:</div>',
                unsafe_allow_html=True,
            )
            termo_busca = st.text_input(
                "Digite o Protocolo ou E-mail",
                placeholder="Ex: F4-PXA6A4 ou seuemail@empresa.com"
            )

            if st.button("🔍 Pesquisar"):
                termo_limpo = termo_busca.strip()
                
                if not termo_limpo:
                    st.warning("⚠️ Digite um número de protocolo ou e-mail para pesquisar.")
                else:
                    if supabase:
                        # 1. Busca por E-MAIL (Histórico dos últimos 15 dias)
                        if "@" in termo_limpo:
                            data_limite = (datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d")
                            res = (
                                supabase.table("chamados")
                                .select("*")
                                .ilike("email_solicitante", termo_limpo)
                                .gte("created_at", data_limite)
                                .order("id", desc=True)
                                .execute()
                            )
                            st.session_state["resultado_busca"] = res.data

                        # 2. Busca por PROTOCOLO EXATO (ex: F4-X8K92P ou #F4-X8K92P)
                        else:
                            proto_exato = termo_limpo if termo_limpo.startswith("#") else f"#{termo_limpo}"
                            res = (
                                supabase.table("chamados")
                                .select("*")
                                .eq("protocolo", proto_exato)
                                .execute()
                            )
                            st.session_state["resultado_busca"] = res.data

            # EXIBIÇÃO DOS RESULTADOS ENCONTRADOS
            if "resultado_busca" in st.session_state and st.session_state["resultado_busca"] is not None:
                resultados = st.session_state["resultado_busca"]
                
                if not resultados:
                    st.error("❌ Nenhum chamado foi encontrado com essa informação.")
                else:
                    st.markdown("### 📋 Resultado da Consulta:")
                    
                    col_widths = [1.1, 1.2, 1.6, 1.2, 1.3, 1.1, 1.4, 1.8, 1.5]
                    headers = ["Protocolo", "Solicitante", "E-mail", "Empresa", "Ferramenta", "Severidade", "Assunto", "Descrição", "Status"]
                    
                    cols_head = st.columns(col_widths)
                    for col, h in zip(cols_head, headers):
                        col.markdown(f'<div class="header-box">{h}</div>', unsafe_allow_html=True)

                    st.markdown("<br>", unsafe_allow_html=True)

                    for c in resultados:
                        with st.container():
                            st.markdown('<div class="chamado-card-container">', unsafe_allow_html=True)
                            
                            c_proto, c_nome, c_mail, c_emp, c_ferr, c_sev, c_ass, c_desc, c_stat = st.columns(col_widths)
                            
                            c_proto.markdown(f'<div class="celula-protocolo"><span class="mobile-label">Protocolo:</span>{c.get("protocolo", "-")}</div>', unsafe_allow_html=True)
                            c_nome.markdown(f'<div class="celula-texto"><span class="mobile-label">Solicitante:</span>{c.get("nome_solicitante", "-")}</div>', unsafe_allow_html=True)
                            c_mail.markdown(f'<div class="celula-texto"><span class="mobile-label">E-mail:</span>{c.get("email_solicitante", "-")}</div>', unsafe_allow_html=True)
                            c_emp.markdown(f'<div class="celula-texto"><span class="mobile-label">Empresa:</span>{c.get("empresa", "-")}</div>', unsafe_allow_html=True)
                            c_ferr.markdown(f'<div class="celula-texto"><span class="mobile-label">Ferramenta:</span>{c.get("ferramenta", "-")}</div>', unsafe_allow_html=True)
                            c_sev.markdown(f'<div class="celula-texto"><span class="mobile-label">Severidade:</span>{c.get("severidade", "-")}</div>', unsafe_allow_html=True)
                            c_ass.markdown(f'<div class="celula-texto"><span class="mobile-label">Assunto:</span>{c.get("assunto", "-")}</div>', unsafe_allow_html=True)
                            c_desc.markdown(f'<div class="celula-texto"><span class="mobile-label">Descrição:</span>{c.get("descricao", "-")}</div>', unsafe_allow_html=True)
                            
                            # STATUS APENAS COMO SELO LEITURA (SEM EDITAR)
                            c_stat.markdown(f'<div class="badge-status"><span class="mobile-label">Status:</span>📌 {c.get("status", "-")}</div>', unsafe_allow_html=True)

                            st.markdown('</div>', unsafe_allow_html=True)

            if st.button("← Voltar ao Menu"):
                st.session_state["opcao_menu"] = "inicio"
                if "resultado_busca" in st.session_state:
                    del st.session_state["resultado_busca"]
                st.rerun()

        elif st.session_state["opcao_menu"] == "avaliar":
            st.markdown(
                '<div class="fala-titulo-card">⭐ Deixe sua avaliação:</div>',
                unsafe_allow_html=True,
            )
            proto_input = st.text_input(
                "Número do Protocolo Concluído",
                placeholder="Ex: F4-PXA6A4"
            )

            if st.button("🔍 Buscar Chamado"):
                termo_limpo = proto_input.strip()
                if not termo_limpo:
                    st.warning("⚠️ Por favor, informe o número do protocolo.")
                else:
                    proto_exato = termo_limpo if termo_limpo.startswith("#") else f"#{termo_limpo}"
                    if supabase:
                        res = (
                            supabase.table("chamados")
                            .select("*")
                            .eq("protocolo", proto_exato)
                            .execute()
                        )
                        if res.data:
                            st.session_state["chamado_para_avaliar"] = res.data[0]
                        else:
                            st.session_state["chamado_para_avaliar"] = None
                            st.error("❌ Nenhum chamado foi encontrado com esse número de protocolo.")

            # Se encontrou o chamado, exibe os detalhes e a interface de avaliação
            if "chamado_para_avaliar" in st.session_state and st.session_state["chamado_para_avaliar"]:
                chamado = st.session_state["chamado_para_avaliar"]
                status_atual = str(chamado.get("status", "")).strip().lower()

                # Permite avaliação para 'concluído' OU 'encerrado pelo solicitante'
                status_permitidos = ["concluído", "concluido", "encerrado pelo solicitante"]

                if status_atual not in status_permitidos:
                    st.warning(
                        f"⚠️ Este chamado está com o status **'{chamado.get('status')}'**. "
                        "Apenas chamados **Concluídos** ou **Encerrados** podem ser avaliados."
                    )
                else:
                    st.markdown(
                        f"""
                        <div class="chamado-card-container" style="margin-top: 15px;">
                            <div class="celula-protocolo">Protocolo: {chamado.get('protocolo')}</div>
                            <div class="celula-texto"><b>Solicitante:</b> {chamado.get('nome_solicitante')}</div>
                            <div class="celula-texto"><b>Assunto:</b> {chamado.get('assunto')}</div>
                            <div class="badge-status" style="margin-top: 8px;">📌 {chamado.get('status')}</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                    st.markdown("#### Como foi o seu atendimento?")
                    
                    # Componente nativo de Estrelas (disponível no Streamlit recente)
                    # Caso sua versão do Streamlit não tenha st.feedback, usamos um seletor numérico amigável
                    try:
                        nota_estrelas = st.feedback("stars")
                        # st.feedback retorna de 0 a 4, ajustamos para 1 a 5 estrelas
                        nota_final = (nota_estrelas + 1) if nota_estrelas is not None else 5
                    except AttributeError:
                        nota_final = st.slider("Selecione de 1 a 5 Estrelas ⭐", min_value=1, max_value=5, value=5)

                    comentario = st.text_area(
                        "Opções de melhoria / Comentários (Opcional)",
                        placeholder="Conte-nos o que achou do atendimento ou o que podemos melhorar..."
                    )

                    if st.button("🚀 Enviar Avaliação"):
                        if supabase:
                            supabase.table("chamados").update({
                                "nota_avaliacao": nota_final,
                                "comentario_avaliacao": comentario.strip()
                            }).eq("protocolo", chamado.get("protocolo")).execute()

                            st.success("🎉 Muito obrigado! Sua avaliação foi enviada com sucesso.")
                            del st.session_state["chamado_para_avaliar"]

            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("← Voltar ao Menu"):
                st.session_state["opcao_menu"] = "inicio"
                if "chamado_para_avaliar" in st.session_state:
                    del st.session_state["chamado_para_avaliar"]
                st.rerun()