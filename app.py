import random
import string
import hashlib
import streamlit as st
from datetime import datetime, timedelta, timezone
from supabase import create_client, Client
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# 1. Configuração da página
st.set_page_config(
    page_title="HelpDesk",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Lista de Atendentes disponíveis para atribuição
OPCOES_ATENDENTES = [
    "Não atribuído",
    "Felipe",
    "Rafael",
]

# ---------------------------------------------------------
# CONEXÃO SUPABASE
# ---------------------------------------------------------
SUPABASE_URL = "https://dmucssgskmhpqdkyovwc.supabase.co"
SUPABASE_KEY = "sb_publishable_sfUWEI0jRY36Hh1iRGeDEA_6MaBTPIy"

# ---------------------------------------------------------
# CONFIGURAÇÕES DE E-MAIL (SECRETS)
# ---------------------------------------------------------
try:
    SMTP_SERVER = st.secrets["email"]["smtp_server"]
    SMTP_PORT = int(st.secrets["email"]["smtp_port"])
    EMAIL_REMETENTE = st.secrets["email"]["email_remetente"]
    SENHA_REMETENTE = st.secrets["email"]["senha_remetente"]
except Exception:
    SMTP_SERVER = "smtp.gmail.com"
    SMTP_PORT = 587
    EMAIL_REMETENTE = "suportef4.helpdesk@gmail.com"
    SENHA_REMETENTE = "ffptwuvezblvtmmk"

@st.cache_resource
def init_supabase():
    try:
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception:
        return None

supabase = init_supabase()

# ---------------------------------------------------------
# FUNÇÕES AUXILIARES E AUTENTICAÇÃO
# ---------------------------------------------------------
def hash_senha(senha):
    return hashlib.sha256(senha.encode("utf-8")).hexdigest()

def buscar_usuario_admin(usuario):
    if supabase:
        res = (
            supabase.table("usuarios_admin")
            .select("usuario, senha, email")
            .eq("usuario", usuario.strip().lower())
            .execute()
        )
        return res.data[0] if res.data else None
    return None

def buscar_email_admin(usuario):
    registro = buscar_usuario_admin(usuario)
    return registro.get("email") if registro else None

def verificar_login(usuario, senha):
    registro = buscar_usuario_admin(usuario)
    if not registro:
        return False
    return registro["senha"] == hash_senha(senha)

def atualizar_senha_admin(usuario, nova_senha):
    if supabase:
        supabase.table("usuarios_admin").update(
            {
                "senha": hash_senha(nova_senha),
                "atualizado_em": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("usuario", usuario.strip().lower()).execute()

def gerar_senha_temporaria(tamanho=8):
    caracteres = string.ascii_letters + string.digits
    return "".join(random.choices(caracteres, k=tamanho))

def listar_usuarios_admin_detalhado():
    if supabase:
        res = (
            supabase.table("usuarios_admin")
            .select("usuario, email, criado_por, created_at")
            .order("created_at", desc=True)
            .execute()
        )
        return res.data if res.data else []
    return []

def adicionar_usuario_admin(usuario, email, criado_por):
    usuario_norm = usuario.strip().lower()

    if buscar_usuario_admin(usuario_norm):
        return {"ok": False, "erro": "Já existe um administrador com esse nome de usuário."}

    senha_temp = gerar_senha_temporaria()

    if supabase:
        supabase.table("usuarios_admin").insert(
            {
                "usuario": usuario_norm,
                "email": email.strip(),
                "senha": hash_senha(senha_temp),
                "criado_por": criado_por,
            }
        ).execute()

    email_enviado = enviar_email_novo_admin(email.strip(), usuario_norm, senha_temp)

    return {
        "ok": True,
        "usuario": usuario_norm,
        "email_enviado": email_enviado,
        "senha_temp": senha_temp,
    }

def remover_usuario_admin(usuario):
    if supabase:
        supabase.table("usuarios_admin").delete().eq("usuario", usuario.strip().lower()).execute()

OPCOES_STATUS = [
    "Aguardando atendimento",
    "Em análise",
    "Em atendimento",
    "Concluído",
    "Cancelado",
    "Encerrado pelo solicitante"
]

def listar_empresas():
    if supabase:
        res = supabase.table("empresas_chamados").select("nome").order("nome").execute()
        return [item["nome"] for item in res.data] if res.data else []
    return []

def listar_empresas_detalhado():
    if supabase:
        res = (
            supabase.table("empresas_chamados")
            .select("nome, criado_por, created_at")
            .order("created_at", desc=True)
            .execute()
        )
        return res.data if res.data else []
    return []

def adicionar_empresa(nome, criado_por):
    if supabase:
        supabase.table("empresas_chamados").insert(
            {"nome": nome, "criado_por": criado_por}
        ).execute()

def remover_empresa(nome):
    if supabase:
        supabase.table("empresas_chamados").delete().eq("nome", nome).execute()

def listar_ferramentas():
    if supabase:
        res = supabase.table("ferramentas_chamados").select("nome").order("nome").execute()
        return [item["nome"] for item in res.data] if res.data else []
    return []

def listar_ferramentas_detalhado():
    if supabase:
        res = (
            supabase.table("ferramentas_chamados")
            .select("nome, criado_por, created_at")
            .order("created_at", desc=True)
            .execute()
        )
        return res.data if res.data else []
    return []

def adicionar_ferramenta(nome, criado_por):
    if supabase:
        supabase.table("ferramentas_chamados").insert(
            {"nome": nome, "criado_por": criado_por}
        ).execute()

def remover_ferramenta(nome):
    if supabase:
        supabase.table("ferramentas_chamados").delete().eq("nome", nome).execute()

def gerar_protocolo():
    letras_numeros = string.ascii_uppercase + string.digits
    codigo = "".join(random.choices(letras_numeros, k=6))
    return f"#F4-{codigo}"

def enviar_email_status(email_destino, nome_solicitante, protocolo, assunto_chamado, status_atual):
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = f"F4 Connect HelpDesk <{EMAIL_REMETENTE}>"
        msg["To"] = email_destino
        msg["Subject"] = f"Atualização do Chamado {protocolo} - Status: {status_atual}"

        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333; background-color: #f4f4f9; padding: 20px;">
            <div style="max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 8px; padding: 20px; border: 1px solid #e0e0e0;">
                <h2 style="color: #007aff; text-align: center; margin-bottom: 5px;">🤖 F4 Connect - Help Desk</h2>
                <p style="text-align: center; color: #666; font-size: 14px; margin-top: 0;">Central de Atendimento e Suporte</p>
                <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">

                <p>Olá, <b>{nome_solicitante}</b>!</p>
                <p>Houve uma atualização no seu chamado. Confira os detalhes abaixo:</p>

                <div style="background-color: #f8fafc; border-left: 4px solid #007aff; padding: 15px; margin: 20px 0; border-radius: 4px;">
                    <p style="margin: 6px 0;"><b>Protocolo:</b> <span style="color: #007aff; font-weight: bold;">{protocolo}</span></p>
                    <p style="margin: 6px 0;"><b>Assunto:</b> {assunto_chamado}</p>
                    <p style="margin: 6px 0;"><b>Status Atual:</b> <span style="background-color: #e0f2fe; color: #0369a1; padding: 4px 10px; border-radius: 12px; font-weight: bold; font-size: 13px;">{status_atual}</span></p>
                </div>

                <p>Você pode acompanhar o andamento a qualquer momento acessando o nosso portal.</p>
                <br>
                <p style="margin-bottom: 0;">Atenciosamente,</p>
                <p style="margin-top: 2px;"><b>Equipe de Suporte F4 Connect</b></p>

                <hr style="border: none; border-top: 1px solid #eee; margin: 25px 0 15px 0;">
                <p style="font-size: 11px; color: #999; text-align: center;">Este é um e-mail automático enviado pelo sistema F4 Connect. Por favor, não responda a este e-mail.</p>
            </div>
        </body>
        </html>
        """

        msg.attach(MIMEText(html_body, "html"))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(EMAIL_REMETENTE, SENHA_REMETENTE)
        server.sendmail(EMAIL_REMETENTE, email_destino, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"Erro ao enviar e-mail: {e}")
        return False

def enviar_email_codigo_senha(email_destino, usuario, codigo):
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = f"F4 Connect HelpDesk <{EMAIL_REMETENTE}>"
        msg["To"] = email_destino
        msg["Subject"] = "Código para redefinição de senha - F4 Connect HelpDesk"

        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333; background-color: #f4f4f9; padding: 20px;">
            <div style="max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 8px; padding: 20px; border: 1px solid #e0e0e0;">
                <h2 style="color: #007aff; text-align: center; margin-bottom: 5px;">🤖 F4 Connect - Help Desk</h2>
                <p style="text-align: center; color: #666; font-size: 14px; margin-top: 0;">Redefinição de senha do painel administrativo</p>
                <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">

                <p>Olá, <b>{usuario}</b>!</p>
                <p>Use o código abaixo para redefinir sua senha. Ele é válido por 10 minutos:</p>

                <div style="text-align: center; margin: 24px 0;">
                    <span style="font-size: 32px; font-weight: 800; letter-spacing: 6px; color: #007aff;">{codigo}</span>
                </div>

                <p>Se você não solicitou essa alteração, apenas ignore este e-mail.</p>
                <br>
                <p style="margin-bottom: 0;">Atenciosamente,</p>
                <p style="margin-top: 2px;"><b>Equipe de Suporte F4 Connect</b></p>

                <hr style="border: none; border-top: 1px solid #eee; margin: 25px 0 15px 0;">
                <p style="font-size: 11px; color: #999; text-align: center;">Este é um e-mail automático enviado pelo sistema F4 Connect. Por favor, não responda a este e-mail.</p>
            </div>
        </body>
        </html>
        """

        msg.attach(MIMEText(html_body, "html"))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(EMAIL_REMETENTE, SENHA_REMETENTE)
        server.sendmail(EMAIL_REMETENTE, email_destino, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"Erro ao enviar e-mail de código: {e}")
        return False

def enviar_email_novo_admin(email_destino, usuario, senha_temporaria):
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = f"F4 Connect HelpDesk <{EMAIL_REMETENTE}>"
        msg["To"] = email_destino
        msg["Subject"] = "Seu acesso administrativo - F4 Connect HelpDesk"

        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333; background-color: #f4f4f9; padding: 20px;">
            <div style="max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 8px; padding: 20px; border: 1px solid #e0e0e0;">
                <h2 style="color: #007aff; text-align: center; margin-bottom: 5px;">🤖 F4 Connect - Help Desk</h2>
                <p style="text-align: center; color: #666; font-size: 14px; margin-top: 0;">Acesso ao painel administrativo</p>
                <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">

                <p>Olá!</p>
                <p>Você foi cadastrado como administrador do F4 Connect HelpDesk. Seus dados de acesso:</p>

                <div style="background-color: #f8fafc; border-left: 4px solid #007aff; padding: 15px; margin: 20px 0; border-radius: 4px;">
                    <p style="margin: 6px 0;"><b>Usuário:</b> {usuario}</p>
                    <p style="margin: 6px 0;"><b>Senha temporária:</b> <span style="color: #007aff; font-weight: bold;">{senha_temporaria}</span></p>
                </div>

                <p>Recomendamos trocar essa senha assim que fizer login, usando a opção "🔑 Alterar senha" no painel administrativo.</p>
                <br>
                <p style="margin-bottom: 0;">Atenciosamente,</p>
                <p style="margin-top: 2px;"><b>Equipe de Suporte F4 Connect</b></p>

                <hr style="border: none; border-top: 1px solid #eee; margin: 25px 0 15px 0;">
                <p style="font-size: 11px; color: #999; text-align: center;">Este é um e-mail automático enviado pelo sistema F4 Connect. Por favor, não responda a este e-mail.</p>
            </div>
        </body>
        </html>
        """

        msg.attach(MIMEText(html_body, "html"))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(EMAIL_REMETENTE, SENHA_REMETENTE)
        server.sendmail(EMAIL_REMETENTE, email_destino, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"Erro ao enviar e-mail de novo administrador: {e}")
        return False

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
        "severidade": severidade,
        "status": "Aguardando atendimento"
    }
    if supabase:
        supabase.table("chamados").insert(dados).execute()
    return protocolo

def atualizar_atendente_chamado(protocolo, novo_atendente):
    if supabase:
        supabase.table("chamados").update({"atendente": novo_atendente}).eq("protocolo", protocolo).execute()

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
robo_src = "https://github.com/felipecamboim-bite/f4-connect/releases/download/v1.0/roboanimado__semfundo.gif"
fundo_src = "https://github.com/user-attachments/assets/12833297-9621-4b87-acd7-3a9365b262b7"
sidebar_bg_src = "https://github.com/user-attachments/assets/da6b4431-1574-42f8-9a64-f654b1f498c8"

if "opcao_menu" not in st.session_state:
    st.session_state["opcao_menu"] = "inicio"

if "etapa_abertura" not in st.session_state:
    st.session_state["etapa_abertura"] = 1

if "ultimo_protocolo" not in st.session_state:
    st.session_state["ultimo_protocolo"] = None

if "ultimo_email_falhou" not in st.session_state:
    st.session_state["ultimo_email_falhou"] = False

if "usuario_logado" not in st.session_state:
    st.session_state["usuario_logado"] = None

if "temp_nome" not in st.session_state:
    st.session_state["temp_nome"] = ""

if "temp_empresa" not in st.session_state:
    st.session_state["temp_empresa"] = "Selecione..."

if "aba_admin" not in st.session_state:
    st.session_state["aba_admin"] = "chamados"

if "mostrar_alterar_senha" not in st.session_state:
    st.session_state["mostrar_alterar_senha"] = False

if "codigo_senha_enviado" not in st.session_state:
    st.session_state["codigo_senha_enviado"] = False

# ---------------------------------------------------------
# CSS DA INTERFACE
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

        /* Oculta os botões de recolher sidebar */
        button[kind="header"],
        button[kind="headerNoPadding"],
        [data-testid="baseButton-header"],
        [data-testid="baseButton-headerNoPadding"],
        [data-testid="stSidebarCollapseButton"],
        [data-testid="stSidebarCollapsedControl"],
        [data-testid="collapsedControl"] {{
            display: none !important;
            visibility: hidden !important;
            pointer-events: none !important;
        }}

        /* SIDEBAR AJUSTADA */
        section[data-testid="stSidebar"] {{
            width: 280px !important;
            min-width: 280px !important;
            background-color: #064e3b !important;
            background-image: url("{sidebar_bg_src}") !important;
            background-size: cover !important;
            background-position: center bottom !important;
            background-repeat: no-repeat !important;
            border-right: 1px solid rgba(0, 183, 255, 0.3) !important;
        }}

        section[data-testid="stSidebar"] > div:first-child {{
            padding-top: 2rem !important;
        }}

        section[data-testid="stSidebar"][aria-expanded="false"] {{
            visibility: visible !important;
            width: 280px !important;
            min-width: 280px !important;
            margin-left: 0px !important;
            transform: none !important;
        }}

        section[data-testid="stSidebar"] .stMarkdown,
        section[data-testid="stSidebar"] label {{
            color: #FFFFFF !important;
            font-family: 'Inter', sans-serif !important;
        }}

        section[data-testid="stSidebar"] .stButton > button {{
            width: 100% !important;
            max-width: 100% !important;
            margin-bottom: 8px !important;
            padding: 8px 12px !important;
            border-radius: 8px !important;
            min-height: 36px !important;
            box-shadow: 0 2px 6px rgba(0, 0, 0, 0.25) !important;
            background-color: rgba(10, 25, 47, 0.7) !important;
        }}

        section[data-testid="stSidebar"] .stButton > button p {{
            font-size: 13px !important;
            font-weight: 600 !important;
            white-space: nowrap !important;
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

        /* ROBÔ RESPONSIVO SEM VAZAR A TELA */
        .robo-box {{
            display: flex !important;
            justify-content: center !important;
            align-items: center !important;
            width: 100% !important;
            margin-top: 10px;
        }}

        .robo-box img {{
            width: 100% !important;
            max-width: 420px !important;
            height: auto !important;
            filter: drop-shadow(0px 12px 22px rgba(0,0,0,0.5));
        }}

        [data-testid="stColumns"] {{
            transform: none !important;
            align-items: center !important;
        }}

        /* CARD DE TÍTULO */
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

        /* BOTÕES PRINCIPAIS */
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
            font-size: 16px !important;
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

        .celula-texto {{
            color: #FFFFFF;
            font-family: 'Inter', sans-serif;
            font-size: 14px;
            font-weight: 500;
            word-wrap: break-word;
            padding-top: 4px;
        }}

        .celula-centro {{
            color: #FFFFFF;
            font-family: 'Inter', sans-serif;
            font-size: 14px;
            font-weight: 500;
            word-wrap: break-word;
            padding-top: 4px;
            text-align: center;
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

        .mobile-label {{
            display: none;
            color: #00d4ff;
            font-weight: 800;
            margin-right: 4px;
        }}

        @media (max-width: 1000px) {{
            [data-testid="stHorizontalBlock"]:has(.header-box) {{
                display: none !important;
            }}

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
            if verificar_login(usuario, senha):
                st.session_state["usuario_logado"] = usuario.strip().lower()
                st.success(f"Bem-vindo, {usuario}!")
                st.rerun()
            else:
                st.error("Usuário ou senha incorretos.")
    else:
        st.success(f"👋 Logado como: **{st.session_state['usuario_logado']}**")

        if st.button("📋 Chamados"):
            st.session_state["aba_admin"] = "chamados"
            st.rerun()

        st.markdown("---")

        if st.button("🏢 Cadastrar empresa"):
            if st.session_state["aba_admin"] == "empresa":
                st.session_state["aba_admin"] = "chamados"
            else:
                st.session_state["aba_admin"] = "empresa"
            st.rerun()

        if st.session_state["aba_admin"] == "empresa":
            nova_empresa = st.text_input("Nome da empresa", key="input_nova_empresa")
            if st.button("💾 Salvar empresa", key="salvar_empresa"):
                if nova_empresa.strip():
                    adicionar_empresa(nova_empresa.strip(), st.session_state["usuario_logado"])
                    st.success("Empresa cadastrada!")
                    st.rerun()
                else:
                    st.warning("Digite o nome da empresa.")

        if st.button("🛠️ Cadastrar Ferramenta"):
            if st.session_state["aba_admin"] == "ferramenta":
                st.session_state["aba_admin"] = "chamados"
            else:
                st.session_state["aba_admin"] = "ferramenta"
            st.rerun()

        if st.session_state["aba_admin"] == "ferramenta":
            nova_ferramenta = st.text_input("Nome da ferramenta", key="input_nova_ferramenta")
            if st.button("💾 Salvar ferramenta", key="salvar_ferramenta"):
                if nova_ferramenta.strip():
                    adicionar_ferramenta(nova_ferramenta.strip(), st.session_state["usuario_logado"])
                    st.success("Ferramenta cadastrada!")
                    st.rerun()
                else:
                    st.warning("Digite o nome da ferramenta.")

        if st.button("👤 Cadastrar Administrador"):
            if st.session_state["aba_admin"] == "usuarios":
                st.session_state["aba_admin"] = "chamados"
            else:
                st.session_state["aba_admin"] = "usuarios"
            st.rerun()

        if st.session_state["aba_admin"] == "usuarios":
            novo_admin_usuario = st.text_input("Nome de usuário", key="input_novo_admin_usuario")
            novo_admin_email = st.text_input("E-mail", key="input_novo_admin_email")

            if st.button("💾 Cadastrar administrador", key="salvar_novo_admin"):
                if not novo_admin_usuario.strip():
                    st.warning("Digite o nome de usuário.")
                elif not novo_admin_email.strip() or "@" not in novo_admin_email:
                    st.warning("Digite um e-mail válido.")
                else:
                    resultado = adicionar_usuario_admin(
                        novo_admin_usuario, novo_admin_email, st.session_state["usuario_logado"]
                    )
                    if not resultado["ok"]:
                        st.error(resultado["erro"])
                    elif resultado["email_enviado"]:
                        st.success(
                            f"Administrador '{resultado['usuario']}' cadastrado! "
                            f"A senha temporária foi enviada para {novo_admin_email.strip()}."
                        )
                        st.rerun()
                    else:
                        st.warning(
                            f"Administrador '{resultado['usuario']}' cadastrado, mas não foi possível "
                            f"enviar o e-mail. Senha temporária: **{resultado['senha_temp']}**"
                        )

        st.markdown("---")

        if st.button("🔑 Alterar senha"):
            abrir = not st.session_state["mostrar_alterar_senha"]
            st.session_state["mostrar_alterar_senha"] = abrir
            if abrir:
                st.session_state["codigo_senha_enviado"] = False
            st.rerun()

        if st.session_state["mostrar_alterar_senha"]:
            if not st.session_state.get("codigo_senha_enviado"):
                st.caption("Vamos enviar um código para o seu e-mail cadastrado.")
                if st.button("📧 Enviar código por e-mail", key="enviar_codigo_senha"):
                    email_admin = buscar_email_admin(st.session_state["usuario_logado"])
                    if not email_admin:
                        st.error("Não há e-mail cadastrado para este usuário.")
                    else:
                        codigo = "".join(random.choices(string.digits, k=6))
                        if enviar_email_codigo_senha(email_admin, st.session_state["usuario_logado"], codigo):
                            st.session_state["codigo_senha_valor"] = codigo
                            st.session_state["codigo_senha_gerado_em"] = datetime.now(timezone.utc)
                            st.session_state["codigo_senha_enviado"] = True
                            st.success(f"Código enviado para {email_admin}!")
                            st.rerun()
                        else:
                            st.error("Não foi possível enviar o e-mail.")
            else:
                codigo_digitado = st.text_input("Código recebido por e-mail", key="codigo_senha_input")
                nova_senha = st.text_input("Nova senha", type="password", key="nova_senha_input")
                confirmar_senha = st.text_input("Confirmar nova senha", type="password", key="confirmar_senha_input")

                if st.button("💾 Confirmar e trocar senha", key="salvar_nova_senha"):
                    codigo_valido = st.session_state.get("codigo_senha_valor")
                    gerado_em = st.session_state.get("codigo_senha_gerado_em")
                    expirado = gerado_em and (datetime.now(timezone.utc) - gerado_em).total_seconds() > 600

                    if expirado:
                        st.error("Código expirado. Solicite um novo.")
                        st.session_state["codigo_senha_enviado"] = False
                    elif codigo_digitado.strip() != codigo_valido:
                        st.error("Código incorreto.")
                    elif len(nova_senha) < 4:
                        st.warning("A nova senha deve ter pelo menos 4 caracteres.")
                    elif nova_senha != confirmar_senha:
                        st.warning("A confirmação não confere com a nova senha.")
                    else:
                        atualizar_senha_admin(st.session_state["usuario_logado"], nova_senha)
                        st.session_state["mostrar_alterar_senha"] = False
                        st.session_state["codigo_senha_enviado"] = False
                        st.session_state.pop("codigo_senha_valor", None)
                        st.session_state.pop("codigo_senha_gerado_em", None)
                        st.success("Senha alterada com sucesso!")
                        st.rerun()

                if st.button("🔁 Reenviar código", key="reenviar_codigo_senha"):
                    st.session_state["codigo_senha_enviado"] = False
                    st.rerun()

        st.markdown("---")
        if st.button("🚪 Sair (Logout)"):
            st.session_state["usuario_logado"] = None
            st.session_state["aba_admin"] = "chamados"
            st.session_state["mostrar_alterar_senha"] = False
            st.session_state["codigo_senha_enviado"] = False
            st.rerun()

# ---------------------------------------------------------
# INTERFACE PRINCIPAL
# ---------------------------------------------------------
st.markdown(
    '<div class="titulo-topo">HelpDesk</div>',
    unsafe_allow_html=True,
)

@st.fragment
def painel_admin():
    st.markdown("## 📊 Painel de Controle - Central de Chamados")

    chamados = listar_chamados()
    if not chamados:
        st.info("Nenhum chamado cadastrado até o momento.")
        return

    col_widths = [1.3, 1.1, 1.2, 1.6, 1.1, 1.2, 1.1, 1.3, 1.8, 1.5]
    headers = ["Atendente", "Protocolo", "Solicitante", "E-mail", "Empresa", "Ferramenta", "Severidade", "Assunto", "Descrição", "Status"]

    cols_head = st.columns(col_widths)
    for col, h in zip(cols_head, headers):
        col.markdown(f'<div class="header-box">{h}</div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    for c in chamados:
        with st.container():
            st.markdown('<div class="chamado-card-container">', unsafe_allow_html=True)

            c_atend, c_proto, c_nome, c_mail, c_emp, c_ferr, c_sev, c_ass, c_desc, c_stat = st.columns(col_widths)

            atendente_atual = c.get("atendente") or "Não atribuído"
            idx_atend = OPCOES_ATENDENTES.index(atendente_atual) if atendente_atual in OPCOES_ATENDENTES else 0

            novo_atendente = c_atend.selectbox(
                "Atendente",
                OPCOES_ATENDENTES,
                index=idx_atend,
                key=f"atend_{c['protocolo']}",
                label_visibility="collapsed"
            )

            if novo_atendente != atendente_atual:
                atualizar_atendente_chamado(c['protocolo'], novo_atendente)
                st.toast(f"Chamado {c['protocolo']} atribuído para: {novo_atendente}")
                st.rerun(scope="fragment")

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

                email_enviado = enviar_email_status(
                    email_destino=c['email_solicitante'],
                    nome_solicitante=c['nome_solicitante'],
                    protocolo=c['protocolo'],
                    assunto_chamado=c['assunto'],
                    status_atual=novo_status
                )

                if email_enviado:
                    st.toast(f"Status do {c['protocolo']} atualizado para: {novo_status}")
                else:
                    st.toast(
                        f"Status do {c['protocolo']} atualizado, mas o e-mail para o solicitante falhou.",
                        icon="⚠️",
                    )
                st.rerun(scope="fragment")

            st.markdown('</div>', unsafe_allow_html=True)

@st.fragment
def painel_cadastros(tipo):
    if tipo == "empresa":
        st.markdown("## 🏢 Empresas Cadastradas")
        itens = listar_empresas_detalhado()
        func_remover = remover_empresa
    else:
        st.markdown("## 🛠️ Ferramentas Cadastradas")
        itens = listar_ferramentas_detalhado()
        func_remover = remover_ferramenta

    if not itens:
        st.info("Nenhum cadastro encontrado até o momento.")
        return

    col_widths = [2.5, 2, 2, 0.8]
    headers = ["Nome", "Usuário", "Data de Cadastro", ""]

    cols_head = st.columns(col_widths)
    for col, h in zip(cols_head, headers):
        col.markdown(f'<div class="header-box">{h}</div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    for item in itens:
        with st.container():
            st.markdown('<div class="chamado-card-container">', unsafe_allow_html=True)

            c_nome, c_user, c_data, c_del = st.columns(col_widths)

            data_formatada = "-"
            if item.get("created_at"):
                try:
                    data_formatada = datetime.fromisoformat(
                        item["created_at"].replace("Z", "+00:00")
                    ).strftime("%d/%m/%Y %H:%M")
                except Exception:
                    data_formatada = item["created_at"]

            c_nome.markdown(f'<div class="celula-centro"><span class="mobile-label">Nome:</span>{item.get("nome", "-")}</div>', unsafe_allow_html=True)
            c_user.markdown(f'<div class="celula-centro"><span class="mobile-label">Usuário:</span>{item.get("criado_por") or "-"}</div>', unsafe_allow_html=True)
            c_data.markdown(f'<div class="celula-centro"><span class="mobile-label">Data:</span>{data_formatada}</div>', unsafe_allow_html=True)

            if c_del.button("🗑️", key=f"del_{tipo}_{item.get('nome')}"):
                func_remover(item.get("nome"))
                st.toast(f"'{item.get('nome')}' removido.")
                st.rerun(scope="fragment")

            st.markdown('</div>', unsafe_allow_html=True)

@st.fragment
def painel_usuarios_admin():
    st.markdown("## 👤 Administradores Cadastrados")

    itens = listar_usuarios_admin_detalhado()

    if not itens:
        st.info("Nenhum administrador cadastrado até o momento.")
        return

    col_widths = [1.8, 2.5, 1.8, 1.8, 0.8]
    headers = ["Usuário", "E-mail", "Cadastrado por", "Data de Cadastro", ""]

    cols_head = st.columns(col_widths)
    for col, h in zip(cols_head, headers):
        col.markdown(f'<div class="header-box">{h}</div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    for item in itens:
        with st.container():
            st.markdown('<div class="chamado-card-container">', unsafe_allow_html=True)

            c_user, c_mail, c_por, c_data, c_del = st.columns(col_widths)

            data_formatada = "-"
            if item.get("created_at"):
                try:
                    data_formatada = datetime.fromisoformat(
                        item["created_at"].replace("Z", "+00:00")
                    ).strftime("%d/%m/%Y %H:%M")
                except Exception:
                    data_formatada = item["created_at"]

            c_user.markdown(f'<div class="celula-centro"><span class="mobile-label">Usuário:</span>{item.get("usuario", "-")}</div>', unsafe_allow_html=True)
            c_mail.markdown(f'<div class="celula-centro"><span class="mobile-label">E-mail:</span>{item.get("email") or "-"}</div>', unsafe_allow_html=True)
            c_por.markdown(f'<div class="celula-centro"><span class="mobile-label">Cadastrado por:</span>{item.get("criado_por") or "-"}</div>', unsafe_allow_html=True)
            c_data.markdown(f'<div class="celula-centro"><span class="mobile-label">Data:</span>{data_formatada}</div>', unsafe_allow_html=True)

            usuario_da_linha = item.get("usuario")
            eh_voce_mesmo = usuario_da_linha == st.session_state["usuario_logado"]
            eh_ultimo_admin = len(itens) <= 1

            if eh_voce_mesmo or eh_ultimo_admin:
                c_del.markdown('<div class="celula-centro">—</div>', unsafe_allow_html=True)
            else:
                if c_del.button("🗑️", key=f"del_admin_{usuario_da_linha}"):
                    remover_usuario_admin(usuario_da_linha)
                    st.toast(f"Administrador '{usuario_da_linha}' removido.")
                    st.rerun(scope="fragment")

            st.markdown('</div>', unsafe_allow_html=True)

if st.session_state["usuario_logado"]:
    if st.session_state["aba_admin"] == "empresa":
        painel_cadastros("empresa")
    elif st.session_state["aba_admin"] == "ferramenta":
        painel_cadastros("ferramenta")
    elif st.session_state["aba_admin"] == "usuarios":
        painel_usuarios_admin()
    else:
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
                    if st.session_state["ultimo_email_falhou"]:
                        st.warning(
                            "⚠️ Não conseguimos enviar o e-mail de confirmação. "
                            "Guarde o protocolo acima para acompanhar seu chamado."
                        )
                    st.session_state["ultimo_protocolo"] = None
                    st.session_state["ultimo_email_falhou"] = False

                empresas_cadastradas = listar_empresas()
                empresa = st.selectbox(
                    "Qual empresa você faz parte?",
                    ["Selecione..."] + empresas_cadastradas + ["Outra"],
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
                ferramentas_cadastradas = listar_ferramentas()
                ferramenta = st.selectbox(
                    "Escolha a ferramenta que necessita de ajuda",
                    ["Selecione..."] + ferramentas_cadastradas + ["Outro"],
                )

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
                            severidade
                        )
                        email_enviado = enviar_email_status(
                            email_destino=email,
                            nome_solicitante=st.session_state["temp_nome"],
                            protocolo=protocolo,
                            assunto_chamado=assunto,
                            status_atual="Aguardando atendimento"
                        )
                        st.session_state["ultimo_protocolo"] = protocolo
                        st.session_state["ultimo_email_falhou"] = not email_enviado
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
                        else:
                            proto_exato = termo_limpo if termo_limpo.startswith("#") else f"#{termo_limpo}"
                            res = (
                                supabase.table("chamados")
                                .select("*")
                                .eq("protocolo", proto_exato)
                                .execute()
                            )
                            st.session_state["resultado_busca"] = res.data

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

            if "chamado_para_avaliar" in st.session_state and st.session_state["chamado_para_avaliar"]:
                chamado = st.session_state["chamado_para_avaliar"]
                status_atual = str(chamado.get("status", "")).strip().lower()

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

                    try:
                        nota_estrelas = st.feedback("stars")
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