import random
import string
import hashlib
import html
import re
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
    # Caso rode localmente sem o secrets configurado, usa o padrão:
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
# LOGIN E SENHA DOS ADMINISTRADORES (armazenados no Supabase,
# permitindo que cada administrador troque sua própria senha)
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
    """
    Cadastra um novo administrador com uma senha temporária aleatória,
    que é enviada para o e-mail informado. Retorna um dicionário com o
    resultado da operação.
    """
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

# ---------------------------------------------------------
# CONTAS DE SOLICITANTES (login público, com aprovação do admin)
# ---------------------------------------------------------
# Pedido do usuário: agora o acesso à tela de abrir/acompanhar/avaliar
# chamado também exige login. Como não existe uma base pra validar quem é
# quem, a própria pessoa cria a conta ("Criar conta"), e ela fica pendente
# até um administrador aprovar manualmente (ver notificação flutuante no
# painel admin). Tabela separada da "usuarios_admin", pra não misturar
# administrador com solicitante comum.
REGEX_USUARIO_SOLICITANTE = re.compile(r"^[a-z]+(\.[a-z]+)*$")

def validar_formato_usuario_solicitante(nome_usuario):
    """Só letras minúsculas, sem espaço; nomes compostos separados por ponto
    (ex: felipe.rodrigues)."""
    return bool(REGEX_USUARIO_SOLICITANTE.match(nome_usuario or ""))

def validar_formato_senha_solicitante(senha):
    """Precisa ter pelo menos 1 caractere especial (número é opcional)."""
    return bool(senha) and bool(re.search(r"[^A-Za-z0-9]", senha))

def buscar_solicitante(nome_usuario):
    if supabase:
        res = (
            supabase.table("solicitantes")
            .select("nome_usuario, email, senha, status")
            .eq("nome_usuario", (nome_usuario or "").strip().lower())
            .execute()
        )
        return res.data[0] if res.data else None
    return None

def criar_solicitacao_conta(nome_usuario, email, senha):
    nome_norm = (nome_usuario or "").strip().lower()
    email_norm = (email or "").strip()

    if not validar_formato_usuario_solicitante(nome_norm):
        return {
            "ok": False,
            "erro": "Nome de usuário inválido. Use só letras minúsculas, sem espaço "
                    "(nomes compostos separados por ponto, ex: felipe.rodrigues).",
        }
    if not validar_formato_senha_solicitante(senha):
        return {"ok": False, "erro": "A senha precisa ter pelo menos 1 caractere especial."}
    if not email_norm or "@" not in email_norm:
        return {"ok": False, "erro": "Digite um e-mail corporativo válido."}
    if buscar_solicitante(nome_norm) or buscar_usuario_admin(nome_norm):
        return {"ok": False, "erro": "Já existe uma conta com esse nome de usuário."}

    if supabase:
        supabase.table("solicitantes").insert(
            {
                "nome_usuario": nome_norm,
                "email": email_norm,
                "senha": hash_senha(senha),
                "status": "pendente",
            }
        ).execute()

    return {"ok": True}

def listar_solicitantes_pendentes():
    if supabase:
        res = (
            supabase.table("solicitantes")
            .select("nome_usuario, email, created_at")
            .eq("status", "pendente")
            .order("created_at", desc=False)
            .execute()
        )
        return res.data if res.data else []
    return []

def aprovar_solicitante(nome_usuario):
    registro = buscar_solicitante(nome_usuario)
    if supabase:
        supabase.table("solicitantes").update({"status": "aprovado"}).eq(
            "nome_usuario", nome_usuario
        ).execute()
    if registro and registro.get("email"):
        enviar_email_conta_solicitante_aprovada(registro["email"], nome_usuario)

def rejeitar_solicitante(nome_usuario):
    # Pedido recusado: some da lista, sem enviar nenhum e-mail (pedido do usuário).
    if supabase:
        supabase.table("solicitantes").delete().eq("nome_usuario", nome_usuario).execute()

def verificar_login_solicitante(nome_usuario, senha):
    """
    Retorna "ok" (login válido e conta aprovada), "pendente" (conta existe
    mas ainda aguarda aprovação) ou "invalido" (usuário/senha não bate ou
    não existe).
    """
    registro = buscar_solicitante(nome_usuario)
    if not registro or registro["senha"] != hash_senha(senha):
        return "invalido"
    if registro["status"] != "aprovado":
        return "pendente"
    return "ok"

def atualizar_senha_solicitante(nome_usuario, nova_senha):
    if supabase:
        supabase.table("solicitantes").update(
            {"senha": hash_senha(nova_senha)}
        ).eq("nome_usuario", (nome_usuario or "").strip().lower()).execute()

OPCOES_STATUS = [
    "Aguardando atendimento",
    "Em análise",
    "Em atendimento",
    "Concluído",
    "Cancelado",
    "Encerrado pelo solicitante"
]

# ---------------------------------------------------------
# EMPRESAS E FERRAMENTAS CADASTRADAS PELO ADMIN
# ---------------------------------------------------------
def listar_empresas():
    """Retorna apenas os nomes, usado para popular os selectboxes do solicitante."""
    if supabase:
        res = supabase.table("empresas_chamados").select("nome").order("nome").execute()
        return [item["nome"] for item in res.data] if res.data else []
    return []

def listar_empresas_detalhado():
    """Retorna nome, quem cadastrou e a data, usado no painel administrativo."""
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
    """Retorna apenas os nomes, usado para popular os selectboxes do solicitante."""
    if supabase:
        res = supabase.table("ferramentas_chamados").select("nome").order("nome").execute()
        return [item["nome"] for item in res.data] if res.data else []
    return []

def listar_ferramentas_detalhado():
    """Retorna nome, quem cadastrou e a data, usado no painel administrativo."""
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

def enviar_email_conta_solicitante_aprovada(email_destino, nome_usuario):
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = f"F4 Connect HelpDesk <{EMAIL_REMETENTE}>"
        msg["To"] = email_destino
        msg["Subject"] = "Sua conta foi criada - F4 Connect HelpDesk"

        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333; background-color: #f4f4f9; padding: 20px;">
            <div style="max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 8px; padding: 20px; border: 1px solid #e0e0e0;">
                <h2 style="color: #007aff; text-align: center; margin-bottom: 5px;">🤖 F4 Connect - Help Desk</h2>
                <p style="text-align: center; color: #666; font-size: 14px; margin-top: 0;">Acesso liberado</p>
                <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">

                <p>Olá!</p>
                <p>Sua conta foi criada no F4 Connect HelpDesk. Seu usuário de acesso:</p>

                <div style="background-color: #f8fafc; border-left: 4px solid #007aff; padding: 15px; margin: 20px 0; border-radius: 4px;">
                    <p style="margin: 6px 0;"><b>Usuário:</b> {nome_usuario}</p>
                </div>

                <p>Já pode entrar com o usuário e a senha que você cadastrou.</p>
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
        print(f"Erro ao enviar e-mail de conta aprovada: {e}")
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
        "severidade": severidade, # <--- NOVO CAMPO
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

def atualizar_chamado_solicitante(protocolo, email, empresa, ferramenta, severidade, assunto, descricao):
    """
    Atualiza os campos que o SOLICITANTE pode editar na tabela de
    "Resultado da Consulta" (Acompanhar meu chamado). Protocolo, nome do
    solicitante e status ficam de fora de propósito — não são editáveis
    por ele.
    """
    if supabase:
        supabase.table("chamados").update(
            {
                "email_solicitante": email,
                "empresa": empresa,
                "ferramenta": ferramenta,
                "severidade": severidade,
                "assunto": assunto,
                "descricao": descricao,
            }
        ).eq("protocolo", protocolo).execute()

# ---------------------------------------------------------
# IMAGENS & SESSÃO
# ---------------------------------------------------------
# OBS: já testamos duas vezes usar a arte de capa (verde sólida com a logo num
# canto) como imagem de fundo em tela cheia — o resultado é sempre a tela
# "inundada" de verde, porque a arte em si é só um retângulo de cor sólida,
# não uma textura. Por isso o fundo do portal usa uma cor sólida (a mesma cor
# da marca) direto no CSS, sem depender de nenhum link de imagem externo.

# Logo "F4 HELPDESK" da sidebar: a arte enviada (Imagem_capa_helpdesk.png) era um
# retângulo verde sólido com a marca só num cantinho — usar ela como imagem de
# fundo esticada deixava a tela toda verde. Em vez disso, recortei só a marca
# (fundo transparente) e embuti como base64 direto no código: assim não depende
# de pasta "static/", link externo nem configuração de enableStaticServing —
# funciona sempre, em qualquer deploy.
LOGO_SIDEBAR_B64 = (
    "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"
)
sidebar_src = f"data:image/png;base64,{LOGO_SIDEBAR_B64}"

# Logo nova (ícone + "F4 Helpdesk" + tagline), hospedada num Release do GitHub —
# exibida só na sidebar do computador (ver CSS de .logo-sidebar-box .logo-desktop).
# No celular/tablet a marca antiga acima continua sendo usada, sem alteração.
sidebar_desktop_src = "https://github.com/user-attachments/assets/5e860b6c-ed61-4c29-ba9e-d49ccc8554cc"

# Mesma logo, com o texto "F4 HELPDESK / CONECTANDO AS PONTAS" recolorido de
# branco para preto (o original é branco, feito pra fundo escuro da sidebar) —
# usada só no cabeçalho de boas-vindas da tela inicial pública (fundo branco).
# Hospedada num Release do GitHub, igual as outras artes.
logo_boas_vindas_src = "https://github.com/user-attachments/assets/c90b7644-6935-4aaa-acdc-6371c97c1069"

if "opcao_menu" not in st.session_state:
    st.session_state["opcao_menu"] = "inicio"

if "etapa_abertura" not in st.session_state:
    st.session_state["etapa_abertura"] = 1

if "ultimo_protocolo" not in st.session_state:
    st.session_state["ultimo_protocolo"] = None

# Guarda se o e-mail de confirmação do último chamado falhou, para avisar na tela
if "ultimo_email_falhou" not in st.session_state:
    st.session_state["ultimo_email_falhou"] = False

if "usuario_logado" not in st.session_state:
    st.session_state["usuario_logado"] = None

if "temp_nome" not in st.session_state:
    st.session_state["temp_nome"] = ""

if "temp_empresa" not in st.session_state:
    st.session_state["temp_empresa"] = "Selecione..."

# Controla qual conteúdo aparece na área principal do admin:
# "chamados" (padrão), "empresa" (cadastro/lista de empresas) ou
# "ferramenta" (cadastro/lista de ferramentas)
if "aba_admin" not in st.session_state:
    st.session_state["aba_admin"] = "chamados"

# Controla se o formulário de "Alterar senha" está visível na sidebar
if "mostrar_alterar_senha" not in st.session_state:
    st.session_state["mostrar_alterar_senha"] = False

# Controla se o código de verificação por e-mail já foi enviado nesta sessão
if "codigo_senha_enviado" not in st.session_state:
    st.session_state["codigo_senha_enviado"] = False

# ---------------------------------------------------------
# LOGIN DO SOLICITANTE (tela inicial nova, com "Criar conta")
# ---------------------------------------------------------
# Guarda quem é o solicitante logado ({"nome_usuario": ..., "email": ...})
# — None enquanto ninguém entrou ainda (mostra a tela de login).
if "solicitante_logado" not in st.session_state:
    st.session_state["solicitante_logado"] = None

# Controla se o formulário "Criar conta" está visível na tela de login
if "mostrar_criar_conta" not in st.session_state:
    st.session_state["mostrar_criar_conta"] = False

# Controla se o formulário "Esqueci minha senha" (solicitante) está visível
if "mostrar_esqueci_senha_solicitante" not in st.session_state:
    st.session_state["mostrar_esqueci_senha_solicitante"] = False

# Controla se o código de recuperação de senha do solicitante já foi enviado
if "codigo_senha_solicitante_enviado" not in st.session_state:
    st.session_state["codigo_senha_solicitante_enviado"] = False

# Controla se o painel de solicitações pendentes (balãozinho do admin) está aberto
if "mostrar_pendentes" not in st.session_state:
    st.session_state["mostrar_pendentes"] = False

# ---------------------------------------------------------
# CSS DA INTERFACE & CONTAINERS DA TABELA ADMIN
# ---------------------------------------------------------
# Fundo da tela pública (não logada / tela inicial do solicitante) é branco;
# o painel do administrador (logado) continua na cor #3B3D35.
_tela_publica = not st.session_state["usuario_logado"]
_cor_fundo_app = "#FFFFFF" if _tela_publica else "#3B3D35"
_cor_titulo_topo = "#3B3D35" if _tela_publica else "#FFFFFF"
_cor_label_campo = "#3B3D35" if _tela_publica else "#FFFFFF"

# Pedido do usuário: remover o sidebar só na tela inicial pública, só na
# visualização de computador (celular continua igual). Também some enquanto
# ninguém fez login ainda (nem admin, nem solicitante) — a nova tela de
# login/"Criar conta" fica limpa, sem sidebar.
_ocultar_sidebar_home = _tela_publica and (
    not st.session_state.get("solicitante_logado")
    or st.session_state["opcao_menu"] == "inicio"
)
_css_ocultar_sidebar_home = (
    """
    @media (min-width: 769px) {
        section[data-testid="stSidebar"] {
            display: none !important;
        }
    }
    """
    if _ocultar_sidebar_home
    else ""
)

st.markdown(
    f"""
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&display=swap" rel="stylesheet">

    <style>
        html, body {{
            margin: 0 !important;
            overflow-x: hidden !important;
            scroll-behavior: auto !important;
        }}

        .stApp {{
            background-color: {_cor_fundo_app} !important;
            overflow-x: hidden !important;
        }}

        header[data-testid="stHeader"] {{
            background-color: transparent !important;
            background: transparent !important;
        }}

        /* Impede que a sidebar seja recolhida. O nome exato do botão de colapsar
           muda entre versões do Streamlit, então escondo TODAS as variantes
           conhecidas (antigas e novas) de uma vez, tanto o botão que fica
           dentro da sidebar aberta quanto o que reaparece no canto pra reabrir. */
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

        section[data-testid="stSidebar"] {{
        width: 280px !important;
        min-width: 280px !important;
        background-color: #1D5902 !important;
        border-right: 1px solid rgba(0, 183, 255, 0.3) !important;
        }}

        /* Reforço: mesmo que algum clique consiga acionar o estado "recolhido"
           internamente, a sidebar continua sendo forçada a aparecer do mesmo
           jeito (mesma largura/visibilidade) — trava visual, não só o botão.
           Isso vale só para telas largas (desktop); no celular/tablet essa
           trava é desfeita logo abaixo, pra sidebar poder ser recolhida. */
        section[data-testid="stSidebar"][aria-expanded="false"] {{
            visibility: visible !important;
            width: 280px !important;
            min-width: 280px !important;
            margin-left: 0px !important;
            transform: none !important;
        }}

        /* CELULAR/TABLET: a sidebar fixa de 280px toma quase a tela toda nesses
           aparelhos. Nessa faixa, volta a mostrar o botão de recolher/abrir
           (padrão do Streamlit) e permite a sidebar realmente sumir quando
           recolhida — assim dá pra liberar a tela cheia no celular. No
           desktop (acima de 768px) nada muda: continua sempre travada aberta. */
        @media (max-width: 768px) {{
            button[kind="header"],
            button[kind="headerNoPadding"],
            [data-testid="baseButton-header"],
            [data-testid="baseButton-headerNoPadding"],
            [data-testid="stSidebarCollapseButton"],
            [data-testid="stSidebarCollapsedControl"],
            [data-testid="collapsedControl"] {{
                display: flex !important;
                visibility: visible !important;
                pointer-events: auto !important;
            }}

            section[data-testid="stSidebar"][aria-expanded="false"] {{
                visibility: hidden !important;
                width: 0px !important;
                min-width: 0px !important;
                margin-left: -280px !important;
                transform: translateX(-100%) !important;
            }}

            /* Seta "abrir sidebar" (aparece sobre o fundo branco da tela
               principal, com a sidebar fechada): preta, pra ficar visível.
               Seta "fechar sidebar" (aparece dentro da própria sidebar verde,
               com ela aberta): continua branca. Cobre tanto o ícone em SVG
               quanto o ícone em fonte (Material Symbols) usado em versões
               mais novas do Streamlit — por isso o "*" pegando qualquer
               elemento filho, não só svg/path. */
            [data-testid="stSidebarCollapsedControl"],
            [data-testid="stSidebarCollapsedControl"] *,
            [data-testid="collapsedControl"],
            [data-testid="collapsedControl"] * {{
                color: #000000 !important;
                fill: #000000 !important;
                opacity: 1 !important;
            }}

            [data-testid="stSidebarCollapseButton"],
            [data-testid="stSidebarCollapseButton"] * {{
                color: #FFFFFF !important;
                fill: #FFFFFF !important;
                opacity: 1 !important;
            }}
        }}

        /* Logo no topo da sidebar (tamanho fixo, sem esticar).
           No computador exibe a logo nova (ícone + "F4 Helpdesk" + tagline);
           no celular/tablet continua com a marca antiga, sem mudança. */
        .logo-sidebar-box {{
            display: flex !important;
            justify-content: center !important;
            margin-bottom: 14px !important;
        }}

        .logo-sidebar-box img {{
            max-width: 170px !important;
            height: auto !important;
        }}

        .logo-sidebar-box .logo-desktop {{
            display: none !important;
        }}

        @media (min-width: 769px) {{
            .logo-sidebar-box .logo-mobile {{
                display: none !important;
            }}

            .logo-sidebar-box .logo-desktop {{
                display: block !important;
                max-width: 270px !important;
            }}

            /* Remove o espaço padrão do Streamlit acima do conteúdo da sidebar,
               só no computador, pra logo subir e ocupar o vão vazio no topo */
            section[data-testid="stSidebar"] > div,
            section[data-testid="stSidebar"] [data-testid="stSidebarContent"],
            section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {{
                padding-top: 0rem !important;
            }}

            .logo-sidebar-box {{
                margin-top: 0.25rem !important;
                margin-bottom: 40px !important;
            }}
        }}

        section[data-testid="stSidebar"] .stMarkdown,
        section[data-testid="stSidebar"] label {{
            color: #FFFFFF !important;
            font-family: 'Inter', sans-serif !important;
        }}

        /* Painel administrativo: sidebar sem barra de rolagem à toa, mas
           volta a rolar automaticamente se o conteúdo crescer (algum painel
           aberto empurrando os itens abaixo) e não couber na tela — assim
           nada fica inacessível */
        section[data-testid="stSidebar"],
        section[data-testid="stSidebar"] > div,
        section[data-testid="stSidebar"] [data-testid="stSidebarContent"],
        section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {{
            overflow-y: auto !important;
            overflow-x: hidden !important;
        }}

        /* Aviso "Logado como: ..." em branco */
        section[data-testid="stSidebar"] .stAlert,
        section[data-testid="stSidebar"] .stAlert p,
        section[data-testid="stSidebar"] .stAlert div,
        section[data-testid="stSidebar"] [data-testid="stAlertContentSuccess"] {{
            color: #FFFFFF !important;
        }}

        /* PAINEL ADMINISTRATIVO (pós-login): os botões da sidebar viram só texto
           clicável — sem fundo, sem borda, sem sombra, sem "cara" de botão.
           Ao passar o mouse, ganham um fundo verde-escuro (like "selecionado"),
           e voltam ao normal quando o mouse sai. */
        section[data-testid="stSidebar"] .stButton > button {{
            width: 100% !important;
            max-width: 100% !important;
            margin-bottom: 8px !important;
            padding: 6px 10px !important;
            background: none !important;
            background-color: transparent !important;
            border: none !important;
            border-radius: 8px !important;
            box-shadow: none !important;
            min-height: auto !important;
            text-align: left !important;
            justify-content: flex-start !important;
            transition: background-color 0.15s ease-in-out !important;
        }}

        section[data-testid="stSidebar"] .stButton > button p {{
            font-size: 13px !important;
            font-weight: 600 !important;
            white-space: nowrap !important;
        }}

        section[data-testid="stSidebar"] .stButton > button:hover {{
            background-color: #72A703 !important;
            box-shadow: none !important;
            transform: none !important;
        }}

        /* Campos de Usuário/Senha na sidebar: fundo branco (o resto dos campos
           da tela principal continua com o fundo azul-escuro original) */
        section[data-testid="stSidebar"] .stTextInput input,
        section[data-testid="stSidebar"] .stTextInput div[data-baseweb],
        section[data-testid="stSidebar"] div[data-testid="stTextInputRootElement"] {{
            background-color: #FFFFFF !important;
            border: none !important;
            border-color: transparent !important;
            box-shadow: none !important;
            outline: none !important;
        }}

        /* Com o fundo do campo branco, o texto digitado (herdava branco da regra
           geral) ficava invisível — escurece só o texto digitado nesses campos */
        section[data-testid="stSidebar"] .stTextInput input {{
            color: #0a192f !important;
            caret-color: #0a192f !important;
        }}

        /* Botão "olhinho" (mostrar/ocultar senha): sem fundo/círculo, só o
           ícone em si pintado de escuro (mantendo o tamanho/posição padrão
           do Streamlit, pra não ficar torto/deslocado) */
        section[data-testid="stSidebar"] .stTextInput button {{
            background-color: transparent !important;
            border: none !important;
            color: #0a192f !important;
        }}

        section[data-testid="stSidebar"] .stTextInput button svg,
        section[data-testid="stSidebar"] .stTextInput button svg path {{
            fill: #0a192f !important;
            color: #0a192f !important;
        }}

        section[data-testid="stSidebar"] .stTextInput input:focus,
        section[data-testid="stSidebar"] .stTextInput div[data-baseweb]:focus-within,
        section[data-testid="stSidebar"] div[data-testid="stTextInputRootElement"]:focus-within {{
            border: none !important;
            border-color: transparent !important;
            box-shadow: none !important;
            outline: none !important;
        }}

        /* O Chrome/Edge pintam o campo de escuro (autofill) quando o usuário
           escolhe uma sugestão salva do navegador — isso ignora o CSS normal
           de background-color, precisa desse truque específico pra manter
           o campo branco com o texto legível mesmo depois do autofill. */
        section[data-testid="stSidebar"] .stTextInput input:-webkit-autofill,
        section[data-testid="stSidebar"] .stTextInput input:-webkit-autofill:hover,
        section[data-testid="stSidebar"] .stTextInput input:-webkit-autofill:focus {{
            -webkit-box-shadow: 0 0 0 1000px #FFFFFF inset !important;
            -webkit-text-fill-color: #0a192f !important;
            caret-color: #0a192f !important;
        }}

        /* ========================================================= */
        /* TELA DE LOGIN / CRIAR CONTA (SOLICITANTE) — nova tela       */
        /* inicial. Fica dentro do fluxo público (fundo branco), então  */
        /* reaproveita a mesma largura/centralização usada nos campos   */
        /* de "Identificação Inicial" (max-width 380px) — o fundo       */
        /* branco/sombra dos campos já vem de graça da regra geral      */
        /* ".st-key-conteudo_publico .stTextInput..." mais abaixo, já   */
        /* que essa tela também é renderizada dentro do fluxo público.  */
        /* ========================================================= */
        .st-key-tela_login_solicitante {{
            width: 100% !important;
            max-width: 380px !important;
            margin-left: auto !important;
            margin-right: auto !important;
            display: flex !important;
            flex-direction: column !important;
            align-items: center !important;
        }}

        .st-key-tela_login_solicitante .stTextInput,
        .st-key-tela_login_solicitante .stCaption {{
            width: 100% !important;
        }}

        /* Campos Usuário/Senha da tela de login: saem do azul-escuro padrão
           (herdado do tema do admin) e passam pro mesmo branco levemente
           diferente do fundo já usado nos campos públicos (.st-key-conteudo_publico),
           sem borda colorida — só o campo em si, com uma sombra leve pra dar
           o contraste que identifica que é preenchível. */
        .st-key-tela_login_solicitante .stTextInput input,
        .st-key-tela_login_solicitante .stTextInput div[data-baseweb],
        .st-key-tela_login_solicitante div[data-testid="stTextInputRootElement"] {{
            background-color: #F1F1EA !important;
            color: #24261F !important;
            border: none !important;
            border-color: transparent !important;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15) !important;
            outline: none !important;
        }}

        .st-key-tela_login_solicitante .stTextInput input:focus,
        .st-key-tela_login_solicitante .stTextInput div[data-baseweb]:focus-within,
        .st-key-tela_login_solicitante div[data-testid="stTextInputRootElement"]:focus-within {{
            border: none !important;
            border-color: transparent !important;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15) !important;
            outline: none !important;
        }}

        /* Botões principais da tela de login (Entrar / Criar usuário /
           Confirmar / Voltar / Reenviar): mesmo verde e mesmo tamanho
           compacto já usado nos botões "Avançar"/"Voltar ao Menu" do resto
           do fluxo público. */
        .st-key-login_solicitante_botoes {{
            display: flex !important;
            flex-direction: column !important;
            align-items: center !important;
            width: 100% !important;
        }}

        .st-key-login_solicitante_botoes .stButton {{
            display: flex !important;
            justify-content: center !important;
            width: 100% !important;
        }}

        .st-key-login_solicitante_botoes .stButton > button {{
            background-color: #1D5902 !important;
            border: 1px solid rgba(255, 255, 255, 0.12) !important;
            box-shadow: 0 8px 20px rgba(0, 0, 0, 0.25) !important;
            width: 220px !important;
            max-width: 92% !important;
            padding: 8px 14px !important;
        }}

        .st-key-login_solicitante_botoes .stButton > button p {{
            font-size: 14px !important;
            white-space: nowrap !important;
        }}

        .st-key-login_solicitante_botoes .stButton > button:hover {{
            background-color: #164602 !important;
            border-color: rgba(255, 255, 255, 0.25) !important;
            box-shadow: 0 10px 24px rgba(0, 0, 0, 0.3) !important;
        }}

        /* "Criar conta" / "Esqueci minha senha": discretos, sem cara de
           botão — só texto sublinhado, lado a lado */
        .st-key-links_login_solicitante,
        .st-key-link_sair_solicitante {{
            display: flex !important;
            justify-content: center !important;
            width: 100% !important;
            margin-top: 14px !important;
        }}

        .st-key-links_login_solicitante .stButton > button,
        .st-key-link_sair_solicitante .stButton > button {{
            background: none !important;
            background-color: transparent !important;
            border: none !important;
            box-shadow: none !important;
            width: auto !important;
            max-width: none !important;
            padding: 4px 8px !important;
            min-height: auto !important;
        }}

        .st-key-links_login_solicitante .stButton > button p,
        .st-key-link_sair_solicitante .stButton > button p {{
            color: #1D5902 !important;
            font-size: 13px !important;
            font-weight: 600 !important;
            text-decoration: underline !important;
            white-space: nowrap !important;
        }}

        .st-key-links_login_solicitante .stButton > button:hover,
        .st-key-link_sair_solicitante .stButton > button:hover {{
            background: none !important;
            background-color: transparent !important;
            box-shadow: none !important;
        }}

        /* ========================================================= */
        /* BALÃOZINHO DE NOTIFICAÇÃO (pedidos de "Criar conta"        */
        /* pendentes) — visível só no painel do administrador,         */
        /* flutuando no canto superior direito, igual ícone de         */
        /* mensagem/notificação flutuante.                             */
        /* ========================================================= */
        .st-key-notificacao_pendentes {{
            position: fixed !important;
            top: 14px !important;
            right: 24px !important;
            z-index: 1000000 !important;
            width: auto !important;
        }}

        .st-key-notificacao_pendentes .stButton > button {{
            background-color: #1D5902 !important;
            border: 2px solid #FFFFFF !important;
            border-radius: 999px !important;
            box-shadow: 0 4px 14px rgba(0, 0, 0, 0.35) !important;
            width: auto !important;
            max-width: none !important;
            padding: 6px 16px !important;
            min-height: auto !important;
        }}

        .st-key-notificacao_pendentes .stButton > button p {{
            color: #FFFFFF !important;
            font-size: 14px !important;
            font-weight: 800 !important;
            white-space: nowrap !important;
        }}

        .st-key-painel_pendentes {{
            position: fixed !important;
            top: 62px !important;
            right: 24px !important;
            z-index: 999999 !important;
            width: 380px !important;
            max-width: calc(100vw - 48px) !important;
            max-height: 60vh !important;
            overflow-y: auto !important;
            background-color: #3B3D35 !important;
            border-radius: 12px !important;
            padding: 14px !important;
            box-shadow: 0 12px 32px rgba(0, 0, 0, 0.45) !important;
        }}

        .titulo-pendentes {{
            color: #FFFFFF !important;
            font-family: 'Inter', sans-serif !important;
            font-weight: 800 !important;
            font-size: 14px !important;
            margin-bottom: 10px !important;
        }}

        .st-key-painel_pendentes [data-testid="stCaptionContainer"] {{
            color: #cfcfcf !important;
        }}

        .st-key-painel_pendentes .celula-texto {{
            font-size: 12px !important;
            padding-top: 6px !important;
        }}

        .st-key-painel_pendentes .stButton > button {{
            width: auto !important;
            max-width: none !important;
            padding: 4px 8px !important;
            min-height: auto !important;
            background: none !important;
            background-color: transparent !important;
            border: none !important;
            box-shadow: none !important;
        }}

        @media (max-width: 768px) {{
            .st-key-notificacao_pendentes {{
                top: 8px !important;
                right: 12px !important;
            }}
            .st-key-painel_pendentes {{
                top: 52px !important;
                right: 12px !important;
                left: 12px !important;
                width: auto !important;
            }}
        }}

        .main .block-container {{
            padding-top: 3rem !important;
            padding-bottom: 2rem !important;
            padding-left: 3rem !important;
            padding-right: 3rem !important;
            max-width: 100% !important;
        }}

        @media (max-width: 768px) {{
            .main .block-container {{
                padding-left: 1.25rem !important;
                padding-right: 1.25rem !important;
                padding-top: 2rem !important;
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
            color: {_cor_titulo_topo} !important;
            text-transform: uppercase;
            letter-spacing: clamp(1px, 0.6vw, 5px);
            margin-bottom: clamp(16px, 3vw, 30px);
            text-shadow: 0px 4px 12px rgba(0, 0, 0, 0.7);
        }}

        /* Título de cada tela do admin (Painel de Controle, Empresas,
           Ferramentas e Administradores cadastrados): ocupa o lugar onde
           ficava "HelpDesk" no topo — centralizado, com fonte um pouco menor
           no celular (os textos são mais longos que "HelpDesk"). */
        .titulo-painel-chamados {{
            text-align: center;
            font-family: 'Inter', sans-serif !important;
            font-weight: 900;
            color: #FFFFFF !important;
            font-size: clamp(20px, 3.4vw, 32px);
            margin-bottom: clamp(16px, 3vw, 30px);
            text-shadow: 0px 4px 12px rgba(0, 0, 0, 0.7);
        }}

        @media (max-width: 768px) {{
            .titulo-painel-chamados {{
                font-size: 17px;
            }}
        }}

        /* Cabeçalho da tela inicial pública: logo grande + frase de boas-vindas,
           no lugar do título "HelpDesk" — só aparece na tela inicial de quem
           não está logado (ver uso condicional mais abaixo no Python) */
        .logo-boas-vindas-box {{
            display: flex !important;
            flex-direction: column !important;
            align-items: center !important;
            margin-bottom: clamp(16px, 3vw, 30px);
        }}

        .logo-boas-vindas-box img {{
            max-width: 320px !important;
            width: 70% !important;
            height: auto !important;
            filter: drop-shadow(0px 4px 12px rgba(0, 0, 0, 0.25));
        }}

        .boas-vindas-texto {{
            text-align: center !important;
            font-family: 'Inter', sans-serif !important;
            font-size: clamp(17px, 3.2vw, 22px);
            font-weight: 800;
            color: #3B3D35 !important;
            margin-top: 14px;
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

        /* Espaçamento extra no topo do bloco de conteúdo público (antes ficava
           "colado" no robô que foi removido; sem ele, esse respiro evita que
           o menu/formulário comece grudado no cabeçalho/logo).
           Também centraliza tudo (título, campos, botões) na tela — antes o
           conjunto ficava "centralizado" só porque o robô ao lado empurrava
           visualmente a coluna estreita pro meio; sem o robô, o conteúdo
           ocupa a largura toda e, sem essa regra, tudo cai pra esquerda.
           Vale pra todas as etapas públicas (Identificação Inicial, Detalhes
           do Chamado, Consulte seu chamado, Avaliar atendimento) e pras duas
           telas (PC e celular). */
        .st-key-conteudo_publico {{
            margin-top: 50px !important;
            display: flex !important;
            flex-direction: column !important;
            align-items: center !important;
        }}

        /* Saudação da tela inicial ("Olá! Como posso te ajudar hoje?") sem balão —
           só o texto, sem fundo/borda/sombra (variante do .fala-titulo-card).
           margin: 0 auto centraliza a caixa (que é mais estreita que a tela,
           por causa do max-width) na largura toda da tela — sem essa regra ela
           ficava grudada na esquerda, já que o antigo robô ao lado não existe
           mais pra "empurrar" o conjunto visualmente pro centro. */
        .fala-titulo-sem-balao {{
            width: 100% !important;
            max-width: 460px !important;
            margin-left: auto !important;
            margin-right: auto !important;
            font-size: clamp(17px, 3.2vw, 22px);
            font-weight: 800;
            color: #3B3D35 !important;
            font-family: 'Inter', sans-serif !important;
            text-align: center !important;
            margin-bottom: 22px;
        }}

        /* "Identificação Inicial:" (e títulos parecidos): a caixa agora usa a
           mesma largura (380px) e o mesmo centro dos campos/botões logo
           abaixo, pra a borda esquerda do texto ficar alinhada com a borda
           esquerda deles — em vez do deslocamento fixo de antes, que
           desalinhava o título do resto do formulário. */
        .titulo-identificacao {{
            text-align: left !important;
            max-width: 380px !important;
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

        /* Os 3 botões de menu da tela inicial (Abrir/Acompanhar/Avaliar):
           fundo verde da marca e texto branco. Vale pra computador e celular,
           já que essa regra não é restrita por media query. */
        .st-key-menu_home_botoes .stButton > button {{
            background-color: #1D5902 !important;
            border: 1px solid rgba(255, 255, 255, 0.12) !important;
            box-shadow: 0 8px 20px rgba(0, 0, 0, 0.25) !important;
        }}

        .st-key-menu_home_botoes .stButton > button p {{
            color: #FFFFFF !important;
        }}

        /* Botões centralizados no bloco (flex column + align-items:center).
           Antes isso só valia pro celular (no PC o robô ao lado "empurrava"
           visualmente o conjunto pro centro da coluna estreita que sobrava).
           Como o robô foi removido de vez e o conteúdo passou a ocupar a
           largura toda da tela, sem essa regra os botões ficam grudados na
           esquerda também no PC — por isso agora vale pras duas telas. */
        .st-key-menu_home_botoes {{
            display: flex !important;
            flex-direction: column !important;
            align-items: center !important;
        }}

        /* No computador, largura fixa (baseada no texto mais longo,
           "Acompanhar meu chamado", numa linha só) pra os três ficarem do
           mesmo tamanho. No celular, os três ocupam a largura da coluna
           (mesmo padrão dos outros botões do app), com quebra de linha
           liberada caso a tela seja bem estreita. */
        @media (min-width: 769px) {{
            .st-key-menu_home_botoes .stButton > button {{
                width: 380px !important;
                max-width: 100% !important;
            }}
            .st-key-menu_home_botoes .stButton > button p {{
                white-space: nowrap !important;
            }}
        }}

        @media (max-width: 768px) {{
            /* "vw" tava sendo calculado em cima de uma largura de página
               maior que a tela visível (provavelmente sobra da sidebar
               recolhida), por isso qualquer coisa em vw saía puxada pra
               esquerda. Voltando pro método que já tinha funcionado certinho
               pra centralizar (flex column + align-items:center no bloco,
               regra acima) — e, pra deixar os botões largos (quase toda a
               largura da tela, como no desenho que você mandou), a largura
               agora é uma % do próprio bloco container (não da página/vw),
               então não sofre desse desvio. */
            .st-key-menu_home_botoes .stButton > button {{
                width: 260px !important;
                max-width: 92% !important;
                padding-left: 10px !important;
                padding-right: 10px !important;
            }}
            .st-key-menu_home_botoes .stButton > button p {{
                white-space: nowrap !important;
                font-size: 15px !important;
            }}
        }}

        .st-key-menu_home_botoes .stButton > button:hover {{
            background-color: #164602 !important;
            border-color: rgba(255, 255, 255, 0.2) !important;
            box-shadow: 0 10px 24px rgba(0, 0, 0, 0.3) !important;
        }}

        /* Botões "Avançar" / "Voltar ao Menu" da 1ª etapa de abertura de chamado:
           centralizados (mesma técnica de flex column + align-items:center
           que já funcionava certinho pros 3 botões da tela inicial), cor
           verde da marca #1D5902 */
        .st-key-etapa1_botoes {{
            display: flex !important;
            flex-direction: column !important;
            align-items: center !important;
        }}

        /* Reforço: centraliza também a "linha" de cada botão (o wrapper que o
           Streamlit gera pra cada st.button), caso ela mesma ocupe 100% da
           largura do container acima (nesse caso o align-items:center sozinho
           não centraliza, só o display:flex + justify-content:center aqui
           dentro resolve) */
        .st-key-etapa1_botoes .stButton {{
            display: flex !important;
            justify-content: center !important;
            width: 100% !important;
        }}

        .st-key-etapa1_botoes .stButton > button {{
            background-color: #1D5902 !important;
            border: 1px solid rgba(255, 255, 255, 0.12) !important;
            box-shadow: 0 8px 20px rgba(0, 0, 0, 0.25) !important;
            width: 220px !important;
            max-width: 92% !important;
            padding: 8px 14px !important;
        }}

        /* "Avançar →" e "← Voltar ao Menu" do mesmo tamanho (largura fixa
           baseada no texto mais longo, "← Voltar ao Menu", igual foi feito
           pros 3 botões da tela inicial) */
        .st-key-etapa1_botoes .stButton > button p {{
            font-size: 14px !important;
            white-space: nowrap !important;
        }}

        .st-key-etapa1_botoes .stButton > button:hover {{
            background-color: #164602 !important;
            border-color: rgba(255, 255, 255, 0.25) !important;
            box-shadow: 0 10px 24px rgba(0, 0, 0, 0.3) !important;
        }}

        .stTextInput label, .stSelectbox label, .stTextArea label {{
            font-size: 15px !important;
            font-weight: 600 !important;
            color: {_cor_label_campo} !important;
            font-family: 'Inter', sans-serif !important;
        }}

        .stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb="select"] {{
            font-size: 16px !important; /* 16px trava o zoom automático do Safari/iOS ao tocar no campo */
            border-radius: 8px !important;
            background-color: rgba(10, 25, 47, 0.7) !important;
            color: #FFFFFF !important;
            border: 1px solid rgba(0, 183, 255, 0.3) !important;
        }}

        /* Campos de preenchimento (texto/textarea) e de seleção (selectbox) das
           telas PÚBLICAS (Identificação Inicial, Detalhes do Chamado, Consulte
           seu chamado, Avaliar atendimento): sem borda, fundo num branco levemente
           diferente do branco da tela (pra não "sumir") e com sombra — substitui
           os fundos escuros que essas telas usavam antes (herdados do tema escuro
           do admin, que não combinavam com o fundo branco público).
           Vale pra caixa de fora inteira (não só o <input>/<select> em si) —
           usando o mesmo padrão de seletor "div[data-baseweb]" (sem valor fixo)
           e "stTextInputRootElement" que já funcionava certinho nos campos de
           usuário/senha da sidebar, porque a cor/borda desses componentes fica
           numa caixinha própria por dentro, não no elemento nativo. Escopado no
           .st-key-conteudo_publico pra não afetar os campos do painel admin, que
           continuam com o tema escuro original. */
        .st-key-conteudo_publico .stTextInput input,
        .st-key-conteudo_publico .stTextInput div[data-baseweb],
        .st-key-conteudo_publico div[data-testid="stTextInputRootElement"],
        .st-key-conteudo_publico .stTextArea textarea,
        .st-key-conteudo_publico .stTextArea div[data-baseweb],
        .st-key-conteudo_publico .stSelectbox div[data-baseweb] {{
            background-color: #F1F1EA !important;
            color: #24261F !important;
            border: none !important;
            border-color: transparent !important;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15) !important;
            outline: none !important;
        }}

        /* Texto/ícone de dentro do selectbox (ex: "Selecione...", a setinha) —
           herdavam branco da regra escura antiga e ficavam invisíveis no fundo
           claro novo */
        .st-key-conteudo_publico .stSelectbox div[data-baseweb] * {{
            color: #24261F !important;
            fill: #24261F !important;
        }}

        /* Mantém a mesma aparência (sem borda, com sombra) quando o campo está
           em foco — sem isso, o navegador volta a desenhar um contorno azul
           padrão ao clicar no campo */
        .st-key-conteudo_publico .stTextInput input:focus,
        .st-key-conteudo_publico .stTextInput div[data-baseweb]:focus-within,
        .st-key-conteudo_publico div[data-testid="stTextInputRootElement"]:focus-within,
        .st-key-conteudo_publico .stTextArea textarea:focus,
        .st-key-conteudo_publico .stTextArea div[data-baseweb]:focus-within,
        .st-key-conteudo_publico .stSelectbox div[data-baseweb]:focus-within {{
            border: none !important;
            border-color: transparent !important;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15) !important;
            outline: none !important;
        }}

        /* Campos "Qual empresa você faz parte?" e "Digite seu Nome e Sobrenome":
           estavam esticando quase até a borda da tela — largura mais contida */
        .st-key-select_empresa_etapa1,
        .st-key-select_empresa_etapa1 div[data-baseweb="select"],
        .st-key-input_nome_solicitante,
        .st-key-input_nome_solicitante input {{
            max-width: 380px !important;
        }}

        /* Campo "Digite o Protocolo ou E-mail" (etapa Acompanhar): mais estreito
           (como o campo Nome da etapa Abrir chamado) */
        .st-key-input_busca_protocolo {{
            max-width: 380px !important;
        }}

        /* Botões "Pesquisar" / "Voltar ao Menu" da etapa Acompanhar: mesmo
           padrão dos botões "Avançar" / "Voltar ao Menu" da Identificação
           Inicial (sem azul/borda, cor verde da marca #1D5902, tamanho menor,
           centralizados). Cada botão aqui não tem um container próprio
           envolvendo os dois juntos (são dois st.button() soltos), então a
           centralização é feita direto em cada .st-key-btn_xxx (cada um vira
           uma linha flex de largura toda, com o botão centralizado dentro). */
        .st-key-btn_pesquisar_chamado,
        .st-key-btn_voltar_menu_acompanhar {{
            display: flex !important;
            justify-content: center !important;
        }}

        .st-key-btn_pesquisar_chamado .stButton > button,
        .st-key-btn_voltar_menu_acompanhar .stButton > button {{
            background-color: #1D5902 !important;
            border: 1px solid rgba(255, 255, 255, 0.12) !important;
            box-shadow: 0 8px 20px rgba(0, 0, 0, 0.25) !important;
            width: 220px !important;
            max-width: 92% !important;
            padding: 8px 14px !important;
        }}

        .st-key-btn_pesquisar_chamado .stButton > button p,
        .st-key-btn_voltar_menu_acompanhar .stButton > button p {{
            white-space: nowrap !important;
        }}

        /* Só nessa tela (Consulte seu chamado): mais respiro entre o campo de
           busca e o "Pesquisar" (empurrando ele um pouco pra baixo) e, com
           isso, menos espaço entre "Pesquisar" e "Voltar ao Menu" — os dois
           botões ficam mais próximos um do outro */
        .st-key-btn_pesquisar_chamado {{
            margin-top: 20px !important;
        }}

        .st-key-btn_voltar_menu_acompanhar {{
            margin-top: 2px !important;
        }}

        /* Dica "role a tela para ver sua consulta": aparece só depois de uma
           pesquisa (logo abaixo do "Voltar ao Menu"), apontando pra baixo —
           a tabela de resultado fica mais abaixo na tela e sem essa pista o
           solicitante pode achar que a pesquisa não funcionou */
        .dica-rolar-consulta {{
            text-align: center !important;
            color: #3B3D35 !important;
            font-family: 'Inter', sans-serif !important;
            font-size: 14px !important;
            font-weight: 600 !important;
            margin-top: 22px !important;
            opacity: 0.75;
            animation: dica-rolar-flutua 1.6s ease-in-out infinite;
        }}

        .dica-rolar-consulta .seta {{
            display: block;
            font-size: 20px !important;
            line-height: 1 !important;
            margin-bottom: 4px !important;
        }}

        @keyframes dica-rolar-flutua {{
            0%, 100% {{ transform: translateY(0px); }}
            50% {{ transform: translateY(6px); }}
        }}


        /* Campo "Número do Protocolo Concluído" (etapa Avaliar atendimento):
           mesma largura contida do campo da etapa Consulte seu chamado */
        .st-key-input_protocolo_avaliar,
        .st-key-input_protocolo_avaliar input {{
            max-width: 380px !important;
        }}

        /* Campos de "Detalhes do Chamado" (E-mail, ferramenta, severidade,
           assunto, descrição): sem largura própria, ficavam esticados quase
           de ponta a ponta da tela depois que o robô (que antes limitava esse
           espaço) foi removido. A largura fica só no container que agrupa
           todos eles (cada campo ocupa 100% do que sobrar dentro dele) — mas,
           diferente dos campos soltos da Identificação Inicial, esse container
           (por ser um st.container(), não um widget direto) não centraliza
           sozinho com o align-items:center lá de cima; por isso o
           margin: auto aqui, que centraliza mesmo dentro de um item flex. */
        .st-key-etapa2_campos {{
            width: 100% !important;
            max-width: 460px !important;
            margin-left: auto !important;
            margin-right: auto !important;
        }}

        /* Campo "Opções de melhoria / Comentários" (etapa Avaliar atendimento):
           mesmo ajuste de largura + centralização */
        .st-key-textarea_comentario_avaliacao {{
            width: 100% !important;
            max-width: 460px !important;
            margin-left: auto !important;
            margin-right: auto !important;
        }}

        /* Avisos (st.warning, ex: "Selecione a empresa...") nas telas públicas:
           vinham na largura toda da tela e com o texto branco (herdado do tema
           escuro padrão do app), invisível no fundo amarelo claro do aviso.
           Aqui: caixa mais estreita e centralizada (mesmo padrão dos campos) e
           texto numa cor escura, legível em cima do amarelo. */
        .st-key-conteudo_publico .stAlert {{
            max-width: 460px !important;
            margin-left: auto !important;
            margin-right: auto !important;
            padding: 10px 14px !important;
        }}

        .st-key-conteudo_publico .stAlert p,
        .st-key-conteudo_publico .stAlert div,
        .st-key-conteudo_publico [data-testid="stAlertContentWarning"] {{
            color: #664d03 !important;
            font-size: 14px !important;
        }}

        /* Aviso de "chamado não encontrado" / "formato incompleto" na tela
           Consulte seu chamado: fica FORA do .st-key-conteudo_publico (pra
           não ficar preso na coluna estreita, já que o resultado da consulta
           ocupa a largura toda), então precisa do mesmo tratamento (caixa
           compacta e centralizada) à parte, com os mesmos valores usados nos
           outros avisos do app. */
        .st-key-aviso_busca_chamado .stAlert {{
            max-width: 460px !important;
            margin-left: auto !important;
            margin-right: auto !important;
            padding: 10px 14px !important;
        }}

        .st-key-aviso_busca_chamado .stAlert p,
        .st-key-aviso_busca_chamado .stAlert div {{
            font-size: 14px !important;
        }}

        /* "Buscar Chamado" / "Voltar ao Menu" (Avaliar atendimento): mesma
           centralização dos botões das outras etapas — cada um é um
           st.button() solto, então centraliza direto no .st-key-btn_xxx */
        .st-key-btn_buscar_chamado_avaliar,
        .st-key-btn_voltar_menu_avaliar {{
            display: flex !important;
            justify-content: center !important;
        }}

        .st-key-btn_buscar_chamado_avaliar .stButton > button,
        .st-key-btn_voltar_menu_avaliar .stButton > button {{
            background-color: #1D5902 !important;
            border: 1px solid rgba(255, 255, 255, 0.12) !important;
            box-shadow: 0 8px 20px rgba(0, 0, 0, 0.25) !important;
            width: 220px !important;
            max-width: 92% !important;
            padding: 8px 14px !important;
        }}

        .st-key-btn_buscar_chamado_avaliar .stButton > button p,
        .st-key-btn_voltar_menu_avaliar .stButton > button p {{
            font-size: 14px !important;
            white-space: nowrap !important;
        }}

        .st-key-btn_buscar_chamado_avaliar .stButton > button:hover,
        .st-key-btn_voltar_menu_avaliar .stButton > button:hover {{
            background-color: #164602 !important;
            border-color: rgba(255, 255, 255, 0.25) !important;
            box-shadow: 0 10px 24px rgba(0, 0, 0, 0.3) !important;
        }}

        /* Card com Protocolo/Solicitante/Assunto na etapa Avaliar atendimento:
           fundo #7C845D, textos em branco, sem o azul do protocolo */
        .card-avaliacao-info {{
            width: 100% !important;
            max-width: 460px !important;
            background-color: #7C845D !important;
            border: none !important;
            border-radius: 12px;
            padding: 12px 16px;
            margin-bottom: 10px;
        }}

        .card-avaliacao-info .celula-protocolo,
        .card-avaliacao-info .celula-texto {{
            color: #FFFFFF !important;
        }}

        .card-avaliacao-info .badge-status {{
            background-color: transparent !important;
            border: 1px solid #ABD904 !important;
            color: #ABD904 !important;
        }}

        /* "Como foi o seu atendimento?" com um respiro maior acima */
        .titulo-como-foi {{
            color: #3B3D35 !important;
            font-family: 'Inter', sans-serif !important;
            font-size: 18px;
            font-weight: 800;
            margin-top: 28px !important;
            margin-bottom: 10px !important;
        }}


        /* "Enviar Avaliação": mesmo tom verde da marca e centralizado como os
           outros botões soltos (sem container próprio) */
        .st-key-btn_enviar_avaliacao {{
            display: flex !important;
            justify-content: center !important;
        }}

        .st-key-btn_enviar_avaliacao .stButton > button {{
            background-color: #1D5902 !important;
            border: 1px solid rgba(255, 255, 255, 0.12) !important;
            box-shadow: 0 8px 20px rgba(0, 0, 0, 0.25) !important;
            width: 220px !important;
            max-width: 92% !important;
            padding: 8px 14px !important;
        }}

        .st-key-btn_enviar_avaliacao .stButton > button p {{
            font-size: 14px !important;
            white-space: nowrap !important;
        }}

        .st-key-btn_enviar_avaliacao .stButton > button:hover {{
            background-color: #164602 !important;
            border-color: rgba(255, 255, 255, 0.25) !important;
            box-shadow: 0 10px 24px rgba(0, 0, 0, 0.3) !important;
        }}

        .st-key-btn_pesquisar_chamado .stButton > button p,
        .st-key-btn_voltar_menu_acompanhar .stButton > button p {{
            font-size: 14px !important;
        }}

        .st-key-btn_pesquisar_chamado .stButton > button:hover,
        .st-key-btn_voltar_menu_acompanhar .stButton > button:hover {{
            background-color: #164602 !important;
            border-color: rgba(255, 255, 255, 0.25) !important;
            box-shadow: 0 10px 24px rgba(0, 0, 0, 0.3) !important;
        }}

        /* Botões "Enviar Chamado" / "Voltar Etapa": cor verde da marca #1D5902,
           centralizados (mesma técnica de flex column + align-items:center
           da Identificação Inicial, já que aqui os dois botões dividem um
           container próprio: st.container(key="etapa2_botoes")) */
        .st-key-etapa2_botoes {{
            display: flex !important;
            flex-direction: column !important;
            align-items: center !important;
        }}

        .st-key-etapa2_botoes .stButton {{
            display: flex !important;
            justify-content: center !important;
            width: 100% !important;
        }}

        .st-key-etapa2_botoes .stButton > button {{
            background-color: #1D5902 !important;
            border: 1px solid rgba(255, 255, 255, 0.12) !important;
            box-shadow: 0 8px 20px rgba(0, 0, 0, 0.25) !important;
            width: 220px !important;
            max-width: 92% !important;
            padding: 8px 14px !important;
        }}

        .st-key-etapa2_botoes .stButton > button p {{
            font-size: 14px !important;
            white-space: nowrap !important;
        }}

        .st-key-etapa2_botoes .stButton > button:hover {{
            background-color: #164602 !important;
            border-color: rgba(255, 255, 255, 0.25) !important;
            box-shadow: 0 10px 24px rgba(0, 0, 0, 0.3) !important;
        }}

        .card-sucesso {{
            width: 100% !important;
            max-width: 460px !important;
            margin-left: auto !important;
            margin-right: auto !important;
            background-color: #72A703 !important;
            border: none !important;
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

        /* Variante centralizada, usada na lista de Empresas/Ferramentas Cadastradas */
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

        /* "Resultado da Consulta" (Acompanhar meu chamado): título com um
           respiro maior em relação ao menu/formulário logo acima */
        .titulo-resultado-consulta {{
            color: #3B3D35 !important;
            font-family: 'Inter', sans-serif !important;
            font-size: 22px;
            font-weight: 800;
            margin-top: 48px !important;
            margin-bottom: 16px !important;
        }}

        /* Tabela em formato de planilha (Excel-like): container de fundo
           #7C845D, tabela HTML de verdade — colunas/linhas sempre alinhadas,
           sem sobrepor texto entre células */
        .tabela-consulta-wrap {{
            background-color: #7C845D;
            border-radius: 10px;
            padding: 12px;
            overflow-x: auto;
        }}

        /* No celular/tablet, mesma ideia do Painel de Controle do admin: em vez
           de espremer as 9 colunas ou empilhar em cards, a tabela toda encolhe
           (zoom) mantendo a grade igual à do computador — dá pra usar o zoom
           de pinça do celular pra ler de perto.
           Só o zoom sozinho não bastava aqui: como essa tabela é HTML puro
           (table-layout: fixed, largura 100%), o navegador ainda calculava a
           largura de cada coluna em cima da tela estreita do celular ANTES de
           encolher — daí cada palavra quebrava letra por letra. No celular,
           a tabela passa a ter largura livre (baseada no conteúdo, como no
           computador) e as células não quebram no meio da palavra; o zoom
           encolhe esse resultado "largo" inteiro, do mesmo jeito que fez com
           a grade do Painel de Controle. */
        @media (max-width: 1000px) {{
            .tabela-consulta-wrap {{
                zoom: 0.4;
                overflow-x: auto !important;
            }}
            .tabela-consulta {{
                table-layout: auto !important;
                width: max-content !important;
            }}
            .tabela-consulta th,
            .tabela-consulta td {{
                white-space: nowrap !important;
                word-wrap: normal !important;
                overflow-wrap: normal !important;
            }}
        }}

        .tabela-consulta {{
            width: 100%;
            border-collapse: collapse;
            table-layout: fixed;
            font-family: 'Inter', sans-serif;
        }}

        .tabela-consulta th,
        .tabela-consulta td {{
            border: 1px solid #3B3D35;
            padding: 10px 12px;
            text-align: center;
            font-size: 13px;
            color: #FFFFFF;
            word-wrap: break-word;
            overflow-wrap: break-word;
            vertical-align: top;
        }}

        .tabela-consulta thead th {{
            background-color: #3B3D35;
            color: #FFFFFF;
            font-size: 12px;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            text-align: center;
        }}

        .tabela-consulta tbody tr:nth-child(even) {{
            background-color: rgba(0, 0, 0, 0.12);
        }}

        .tabela-consulta td.col-protocolo {{
            color: #FFFFFF;
            font-weight: 800;
        }}

        /* ========================================================= */
        /* PAINEL DE CONTROLE - CENTRAL DE CHAMADOS, EMPRESAS/         */
        /* FERRAMENTAS CADASTRADAS E ADMINISTRADORES CADASTRADOS:      */
        /* mesmo visual de "planilha" (fundo #7C845D, cabeçalho        */
        /* escuro, texto branco centralizado, sem grade)               */
        /* ========================================================= */
        .st-key-painel_admin_tabela,
        .st-key-painel_cadastros_tabela,
        .st-key-painel_usuarios_admin_tabela {{
            background-color: #7C845D !important;
            border-radius: 10px !important;
            padding: 10px !important;
        }}

        .st-key-painel_admin_tabela [data-testid="stHorizontalBlock"],
        .st-key-painel_cadastros_tabela [data-testid="stHorizontalBlock"],
        .st-key-painel_usuarios_admin_tabela [data-testid="stHorizontalBlock"] {{
            gap: 0 !important;
        }}

        .st-key-painel_admin_tabela [data-testid="stColumn"],
        .st-key-painel_cadastros_tabela [data-testid="stColumn"],
        .st-key-painel_usuarios_admin_tabela [data-testid="stColumn"] {{
            padding: 0 !important;
        }}

        .st-key-painel_admin_tabela .header-box,
        .st-key-painel_cadastros_tabela .header-box,
        .st-key-painel_usuarios_admin_tabela .header-box {{
            background-color: #3B3D35 !important;
            color: #FFFFFF !important;
            border: none !important;
            border-radius: 0 !important;
            box-shadow: none !important;
            font-size: 11px !important;
            margin-bottom: 14px !important;
        }}

        .st-key-painel_admin_tabela .celula-texto,
        .st-key-painel_admin_tabela .celula-protocolo,
        .st-key-painel_cadastros_tabela .celula-centro,
        .st-key-painel_usuarios_admin_tabela .celula-centro {{
            color: #FFFFFF !important;
            text-align: center !important;
            padding: 8px 10px !important;
            font-size: 12px !important;
        }}

        /* Os seletores de Atendente/Status continuam funcionais (não dá pra
           virar texto estático), mas ganham a mesma cor escura do cabeçalho
           pra combinar com a grade */
        .st-key-painel_admin_tabela .stSelectbox div[data-baseweb="select"] {{
            background-color: #3B3D35 !important;
            border: none !important;
            box-shadow: none !important;
            border-radius: 20px !important;
            color: #FFFFFF !important;
            margin: 4px 8px !important;
            font-size: 12px !important;
        }}

        /* Botão "Excluir" (Empresas/Ferramentas e Administradores): compacto,
           combinando com o resto da planilha */
        .st-key-painel_cadastros_tabela .stButton > button,
        .st-key-painel_usuarios_admin_tabela .stButton > button {{
            background-color: #3B3D35 !important;
            color: #FFFFFF !important;
            border: none !important;
            box-shadow: none !important;
            border-radius: 20px !important;
            font-size: 12px !important;
            padding: 4px 10px !important;
            min-height: auto !important;
            margin: 4px 8px !important;
        }}

        .st-key-painel_cadastros_tabela .stButton > button p,
        .st-key-painel_usuarios_admin_tabela .stButton > button p {{
            font-size: 12px !important;
            font-weight: 400 !important;
        }}

        .tabela-consulta .badge-status {{
            background-color: transparent;
            border: 1px solid #FFFFFF;
            color: #FFFFFF;
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

        /* PAINEL ADMINISTRATIVO (Painel de Controle - Central de Chamados) no
           celular/tablet: em vez da lista de cards empilhados (regra geral
           acima), mostra a mesma grade de colunas do computador só que
           "afastada" (encolhida) pra caber na tela — nem que fique miúdo, dá
           pra dar zoom (pinça) pra ler. Só essa tela; as outras (cadastros,
           consulta, avaliação) continuam com os cards empilhados de sempre. */
        @media (max-width: 1000px) {{
            .st-key-painel_admin_tabela [data-testid="stHorizontalBlock"]:has(.header-box),
            .st-key-painel_admin_tabela [data-testid="stHorizontalBlock"]:has(.celula-protocolo) {{
                display: flex !important;
                flex-direction: row !important;
                gap: 6px !important;
            }}
            /* Reproduz as mesmas proporções de coluna do computador
               (Atendente 1.3, Protocolo 1.1, Solicitante 1.2, E-mail 1.6,
               Empresa 1.1, Ferramenta 1.2, Severidade 1.1, Assunto 1.3,
               Descrição 1.8, Status 1.5 — mesmos valores do col_widths do
               Python), já que a regra geral de "vira card empilhado" força
               100%/coluna única e precisa ser desfeita aqui. */
            .st-key-painel_admin_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {{
                width: auto !important;
                min-width: 0 !important;
            }}
            .st-key-painel_admin_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(1)  {{ flex: 1.3 1 0px !important; }}
            .st-key-painel_admin_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(2)  {{ flex: 1.1 1 0px !important; }}
            .st-key-painel_admin_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(3)  {{ flex: 1.2 1 0px !important; }}
            .st-key-painel_admin_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(4)  {{ flex: 1.6 1 0px !important; }}
            .st-key-painel_admin_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(5)  {{ flex: 1.1 1 0px !important; }}
            .st-key-painel_admin_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(6)  {{ flex: 1.2 1 0px !important; }}
            .st-key-painel_admin_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(7)  {{ flex: 1.1 1 0px !important; }}
            .st-key-painel_admin_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(8)  {{ flex: 1.3 1 0px !important; }}
            .st-key-painel_admin_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(9)  {{ flex: 1.8 1 0px !important; }}
            .st-key-painel_admin_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(10) {{ flex: 1.5 1 0px !important; }}

            .st-key-painel_admin_tabela .mobile-label {{
                display: none !important;
            }}

            /* Encolhe a tabela inteira (fica "afastada", como no computador).
               A 1ª tentativa usou transform:scale + largura 200% — só que
               isso alarga a PÁGINA INTEIRA de verdade (as colunas do app têm
               overflow:visible de propósito, pra não cortar menus suspensos
               em outras telas), e o celular reagia dando zoom-out automático
               em tudo (título, sidebar, etc.), não só na tabela. "zoom" (ao
               contrário de transform) encolhe também o espaço reservado no
               layout — sem alargar nada, sem vão sobrando embaixo. Dá pra
               usar o zoom de pinça do celular pra ler de perto. */
            .st-key-painel_admin_tabela {{
                zoom: 0.4;
                overflow-x: auto !important;
            }}
        }}

        /* ========================================================= */
        /* RESULTADO DA CONSULTA (Acompanhar meu chamado): mesmo visual  */
        /* de "planilha" das outras tabelas, mas com campos editáveis    */
        /* pelo solicitante (E-mail, Empresa, Ferramenta, Severidade,    */
        /* Assunto, Descrição). Protocolo/Solicitante/Status continuam   */
        /* somente leitura (pedido do usuário).                          */
        /* ========================================================= */
        .st-key-resultado_consulta_tabela {{
            background-color: #7C845D !important;
            border-radius: 10px !important;
            padding: 10px !important;
        }}

        .st-key-resultado_consulta_tabela [data-testid="stHorizontalBlock"] {{
            gap: 6px !important;
        }}

        .st-key-resultado_consulta_tabela [data-testid="stColumn"] {{
            padding: 2px !important;
        }}

        .st-key-resultado_consulta_tabela .header-box {{
            background-color: #3B3D35 !important;
            color: #FFFFFF !important;
            border: none !important;
            border-radius: 8px !important;
            box-shadow: none !important;
            font-size: 11px !important;
            margin-bottom: 6px !important;
        }}

        /* Badge de Status: mantém o mesmo visual (contorno branco, sem
           preenchimento) que a tabela antiga (.tabela-consulta) já usava,
           em vez do padrão cyan/azul usado no resto do app */
        .st-key-resultado_consulta_tabela .badge-status {{
            background-color: transparent !important;
            border: 1px solid #FFFFFF !important;
            color: #FFFFFF !important;
        }}

        /* Campos editáveis (texto/textarea/seletor de severidade): fundo
           escuro combinando com o resto da planilha, texto branco, compactos */
        .st-key-resultado_consulta_tabela .stTextInput input,
        .st-key-resultado_consulta_tabela .stTextInput div[data-baseweb],
        .st-key-resultado_consulta_tabela div[data-testid="stTextInputRootElement"],
        .st-key-resultado_consulta_tabela .stTextArea textarea,
        .st-key-resultado_consulta_tabela .stTextArea div[data-baseweb],
        .st-key-resultado_consulta_tabela .stSelectbox div[data-baseweb="select"] {{
            background-color: #3B3D35 !important;
            color: #FFFFFF !important;
            border: none !important;
            border-color: transparent !important;
            box-shadow: none !important;
            outline: none !important;
            border-radius: 8px !important;
            font-size: 12px !important;
        }}

        .st-key-resultado_consulta_tabela .stSelectbox div[data-baseweb] * {{
            color: #FFFFFF !important;
            fill: #FFFFFF !important;
        }}

        .st-key-resultado_consulta_tabela .stTextArea textarea {{
            min-height: 68px !important;
        }}

        .st-key-resultado_consulta_tabela .celula-protocolo,
        .st-key-resultado_consulta_tabela .celula-texto {{
            font-size: 12px !important;
            padding: 8px 4px !important;
        }}

        /* Botão "Salvar": compacto, mesmo padrão dos botões "Excluir" das
           outras planilhas administrativas — sobrescreve o estilo padrão
           (largo, azul translúcido) usado nos outros botões da tela pública */
        .st-key-resultado_consulta_tabela .stButton > button {{
            background-color: #3B3D35 !important;
            color: #FFFFFF !important;
            border: none !important;
            box-shadow: none !important;
            border-radius: 20px !important;
            width: auto !important;
            max-width: none !important;
            padding: 6px 12px !important;
            min-height: auto !important;
            margin: 4px 0 !important;
        }}

        .st-key-resultado_consulta_tabela .stButton > button p {{
            font-size: 12px !important;
            font-weight: 600 !important;
            white-space: nowrap !important;
            color: #FFFFFF !important;
        }}

        .st-key-resultado_consulta_tabela .stButton > button:hover {{
            background-color: #52543f !important;
        }}

        /* No celular/tablet: mesma técnica de "afastar" (zoom) já usada nas
           outras planilhas — mantém a grade de colunas do computador em vez
           de empilhar em cards, só encolhida (dá pra usar o zoom de pinça do
           celular pra ler de perto). */
        @media (max-width: 1000px) {{
            .st-key-resultado_consulta_tabela [data-testid="stHorizontalBlock"]:has(.header-box),
            .st-key-resultado_consulta_tabela [data-testid="stHorizontalBlock"]:has(.celula-protocolo) {{
                display: flex !important;
                flex-direction: row !important;
                gap: 6px !important;
            }}
            .st-key-resultado_consulta_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {{
                width: auto !important;
                min-width: 0 !important;
            }}
            .st-key-resultado_consulta_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(1)  {{ flex: 1.1 1 0px !important; }}
            .st-key-resultado_consulta_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(2)  {{ flex: 1.2 1 0px !important; }}
            .st-key-resultado_consulta_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(3)  {{ flex: 1.6 1 0px !important; }}
            .st-key-resultado_consulta_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(4)  {{ flex: 1.1 1 0px !important; }}
            .st-key-resultado_consulta_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(5)  {{ flex: 1.2 1 0px !important; }}
            .st-key-resultado_consulta_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(6)  {{ flex: 1.3 1 0px !important; }}
            .st-key-resultado_consulta_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(7)  {{ flex: 1.3 1 0px !important; }}
            .st-key-resultado_consulta_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(8)  {{ flex: 1.8 1 0px !important; }}
            .st-key-resultado_consulta_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(9)  {{ flex: 1.3 1 0px !important; }}
            .st-key-resultado_consulta_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(10) {{ flex: 0.9 1 0px !important; }}

            .st-key-resultado_consulta_tabela .mobile-label {{
                display: none !important;
            }}

            .st-key-resultado_consulta_tabela {{
                zoom: 0.4;
                overflow-x: auto !important;
            }}
        }}

        {_css_ocultar_sidebar_home}
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------
# SIDEBAR (LOGIN ADMIN)
# ---------------------------------------------------------
with st.sidebar:
    # A logo do sidebar foi removida (pedido do usuário).
    # O texto "Área Administrativa" foi removido (pedido do usuário) — os
    # campos de Usuário/Senha/Entrar não ficam mais aqui na sidebar — foram
    # movidos pro canto superior direito da tela principal (pedido do
    # usuário, layout em PC). Ver bloco "LOGIN ADMIN (CANTO SUPERIOR
    # DIREITO)", logo depois do fechamento dessa sidebar.
    if st.session_state["usuario_logado"]:
        st.success(f"👋 Logado como: **{st.session_state['usuario_logado']}**")

        # Destaca (fundo verde-escuro) a opção da sidebar correspondente à
        # tela que está aberta agora, do mesmo jeito que o efeito de hover
        _mapa_aba_para_key = {
            "chamados": "nav_chamados",
            "empresa": "nav_empresa",
            "ferramenta": "nav_ferramenta",
            "usuarios": "nav_admin",
        }
        _keys_ativas = []
        _key_aba_atual = _mapa_aba_para_key.get(st.session_state["aba_admin"])
        if _key_aba_atual:
            _keys_ativas.append(_key_aba_atual)
        if st.session_state["mostrar_alterar_senha"]:
            _keys_ativas.append("nav_senha")

        if _keys_ativas:
            _seletores_ativos = ", ".join(
                f'.st-key-{k} .stButton > button' for k in _keys_ativas
            )
            st.markdown(
                f"<style>{_seletores_ativos} {{ background-color: #72A703 !important; }}</style>",
                unsafe_allow_html=True,
            )

        if st.button("📋 Chamados", key="nav_chamados"):
            st.session_state["aba_admin"] = "chamados"
            st.rerun()

        st.markdown("---")

        # ---- CADASTRAR EMPRESA ----
        if st.button("🏢 Cadastrar empresa", key="nav_empresa"):
            # clicar de novo no mesmo texto fecha o campo
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

        # ---- CADASTRAR FERRAMENTA ----
        if st.button("🛠️ Cadastrar Ferramenta", key="nav_ferramenta"):
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

        # ---- CADASTRAR ADMINISTRADOR ----
        if st.button("👤 Cadastrar Administrador", key="nav_admin"):
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
                        # e-mail falhou: mostra a senha na tela como último recurso,
                        # sem dar rerun para a mensagem não sumir antes de ser lida
                        st.warning(
                            f"Administrador '{resultado['usuario']}' cadastrado, mas não foi possível "
                            f"enviar o e-mail. Senha temporária: **{resultado['senha_temp']}** "
                            "(repasse com segurança e peça para trocar assim que possível)."
                        )

        st.markdown("---")

        # ---- ALTERAR SENHA (via código enviado por e-mail, sem precisar da senha atual) ----
        if st.button("🔑 Alterar senha", key="nav_senha"):
            abrir = not st.session_state["mostrar_alterar_senha"]
            st.session_state["mostrar_alterar_senha"] = abrir
            if abrir:
                # sempre que reabrir o painel, começa do zero (pede um código novo)
                st.session_state["codigo_senha_enviado"] = False
            st.rerun()

        if st.session_state["mostrar_alterar_senha"]:
            if not st.session_state.get("codigo_senha_enviado"):
                st.caption("Vamos enviar um código para o seu e-mail cadastrado.")
                if st.button("📧 Enviar código por e-mail", key="enviar_codigo_senha"):
                    email_admin = buscar_email_admin(st.session_state["usuario_logado"])
                    if not email_admin:
                        st.error(
                            "Não há e-mail cadastrado para este usuário. "
                            "Peça para outro administrador cadastrar seu e-mail na tabela usuarios_admin."
                        )
                    else:
                        codigo = "".join(random.choices(string.digits, k=6))
                        if enviar_email_codigo_senha(email_admin, st.session_state["usuario_logado"], codigo):
                            st.session_state["codigo_senha_valor"] = codigo
                            st.session_state["codigo_senha_gerado_em"] = datetime.now(timezone.utc)
                            st.session_state["codigo_senha_enviado"] = True
                            st.success(f"Código enviado para {email_admin}!")
                            st.rerun()
                        else:
                            st.error("Não foi possível enviar o e-mail. Tente novamente mais tarde.")
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
        if st.button("🚪 Sair (Logout)", key="nav_logout"):
            st.session_state["usuario_logado"] = None
            st.session_state["aba_admin"] = "chamados"
            st.session_state["mostrar_alterar_senha"] = False
            st.session_state["codigo_senha_enviado"] = False
            st.rerun()

# ---------------------------------------------------------
# INTERFACE PRINCIPAL
# ---------------------------------------------------------
# O login do administrador (Usuário/Senha/Entrar), que ficava fixo no canto
# superior direito da tela, foi removido dali (pedido do usuário) — agora
# TODO login (administrador e solicitante) acontece na nova tela inicial de
# login/"Criar conta" (ver bloco "TELA DE LOGIN (SOLICITANTE)" mais abaixo,
# no "else" final da Visão Pública).
#
# Na tela inicial pública (já logado como solicitante), o título "HelpDesk"
# dá lugar à logo grande + frase de boas-vindas. No fluxo público das outras
# etapas, o título de sempre continua igual. Já logado como admin (qualquer
# uma das 4 telas do admin — Painel de Controle, Empresas, Ferramentas ou
# Administradores cadastrados), o "HelpDesk" some e o próprio título de cada
# tela toma esse lugar no topo (ver painel_admin() / painel_cadastros() /
# painel_usuarios_admin()), pra não ficar duplicado. E, enquanto ninguém fez
# login ainda (nem admin, nem solicitante), essa logo/frase nem aparece aqui
# — a tela de login tem seu próprio cabeçalho, mais simples.
_solicitante_logado = bool(st.session_state.get("solicitante_logado"))

_mostrar_boas_vindas_logo = (
    _solicitante_logado
    and st.session_state["opcao_menu"] == "inicio"
)

if _mostrar_boas_vindas_logo:
    st.markdown(
        f'<div class="logo-boas-vindas-box">'
        f'<img src="{logo_boas_vindas_src}">'
        f'<div class="boas-vindas-texto">Seja bem-vindo ao Helpdesk! No que podemos ajudar?</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
elif _solicitante_logado:
    # Demais etapas públicas (Abrir chamado, Acompanhar, Avaliar): só a logo,
    # sem o texto "HelpDesk" e sem a frase de boas-vindas (que é só da inicial).
    st.markdown(
        f'<div class="logo-boas-vindas-box">'
        f'<img src="{logo_boas_vindas_src}">'
        f'</div>',
        unsafe_allow_html=True,
    )

# ------------------ VISÃO ADMIN (TABELA COM CARDS) ------------------
@st.fragment
def painel_admin():
    # Título centralizado, ocupando o lugar onde ficava "HelpDesk" no topo
    # (que fica escondido só nessa tela — ver _eh_painel_chamados) pra não
    # duplicar título.
    st.markdown(
        '<div class="titulo-painel-chamados">📊 Painel de Controle - Central de Chamados</div>',
        unsafe_allow_html=True,
    )

    chamados = listar_chamados()
    if not chamados:
        st.info("Nenhum chamado cadastrado até o momento.")
        return

    # 1. 10 BLOCOS DE TITULOS/CABEÇALHO
    col_widths = [1.3, 1.1, 1.2, 1.6, 1.1, 1.2, 1.1, 1.3, 1.8, 1.5]
    headers = ["Atendente", "Protocolo", "Solicitante", "E-mail", "Empresa", "Ferramenta", "Severidade", "Assunto", "Descrição", "Status"]

    with st.container(key="painel_admin_tabela"):
        cols_head = st.columns(col_widths)
        for col, h in zip(cols_head, headers):
            col.markdown(f'<div class="header-box">{h}</div>', unsafe_allow_html=True)

        # 2. Exibição das linhas com o Seletor de Atendente
        for c in chamados:
            c_atend, c_proto, c_nome, c_mail, c_emp, c_ferr, c_sev, c_ass, c_desc, c_stat = st.columns(col_widths)

            # --- 1ª COLUNA: SELETOR DE ATENDENTE ---
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

            # SELETOR DE STATUS
            idx_atual = OPCOES_STATUS.index(c['status']) if c['status'] in OPCOES_STATUS else 0
            novo_status = c_stat.selectbox(
                "Status",
                OPCOES_STATUS,
                index=idx_atual,
                key=f"status_{c['protocolo']}",
                label_visibility="collapsed"
            )

            if novo_status != c['status']:
                # 1. Atualiza no Supabase
                atualizar_status_chamado(c['protocolo'], novo_status)

                # 2. --- DISPARA O E-MAIL DE ATUALIZAÇÃO ---
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
                # rerun com escopo "fragment": atualiza só este painel,
                # sem re-executar o app inteiro (login, CSS, imagens etc.)
                st.rerun(scope="fragment")


# ------------------ VISÃO ADMIN: LISTA DE EMPRESAS / FERRAMENTAS CADASTRADAS ------------------
@st.fragment
def painel_cadastros(tipo):
    """
    tipo: "empresa" ou "ferramenta"
    Mostra a lista de itens cadastrados com nome, usuário que cadastrou e data.
    """
    if tipo == "empresa":
        st.markdown(
            '<div class="titulo-painel-chamados">🏢 Empresas Cadastradas</div>',
            unsafe_allow_html=True,
        )
        itens = listar_empresas_detalhado()
        func_remover = remover_empresa
    else:
        st.markdown(
            '<div class="titulo-painel-chamados">🛠️ Ferramentas Cadastradas</div>',
            unsafe_allow_html=True,
        )
        itens = listar_ferramentas_detalhado()
        func_remover = remover_ferramenta

    if not itens:
        st.info("Nenhum cadastro encontrado até o momento.")
        return

    col_widths = [2.5, 2, 2, 0.8]
    headers = ["Nome", "Usuário", "Data de Cadastro", ""]

    with st.container(key="painel_cadastros_tabela"):
        cols_head = st.columns(col_widths)
        for col, h in zip(cols_head, headers):
            col.markdown(f'<div class="header-box">{h}</div>', unsafe_allow_html=True)

        for i, item in enumerate(itens):
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

            if c_del.button("Excluir", key=f"del_{tipo}_{i}_{item.get('nome')}"):
                func_remover(item.get("nome"))
                st.toast(f"'{item.get('nome')}' removido.")
                st.rerun(scope="fragment")


# ------------------ VISÃO ADMIN: LISTA DE ADMINISTRADORES CADASTRADOS ------------------
@st.fragment
def painel_usuarios_admin():
    st.markdown(
        '<div class="titulo-painel-chamados">👤 Administradores Cadastrados</div>',
        unsafe_allow_html=True,
    )

    itens = listar_usuarios_admin_detalhado()

    if not itens:
        st.info("Nenhum administrador cadastrado até o momento.")
        return

    col_widths = [1.8, 2.5, 1.8, 1.8, 0.8]
    headers = ["Usuário", "E-mail", "Cadastrado por", "Data de Cadastro", ""]

    with st.container(key="painel_usuarios_admin_tabela"):
        cols_head = st.columns(col_widths)
        for col, h in zip(cols_head, headers):
            col.markdown(f'<div class="header-box">{h}</div>', unsafe_allow_html=True)

        for item in itens:
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

            # Por segurança: não deixa remover a si mesmo, nem o último administrador restante
            usuario_da_linha = item.get("usuario")
            eh_voce_mesmo = usuario_da_linha == st.session_state["usuario_logado"]
            eh_ultimo_admin = len(itens) <= 1

            if eh_voce_mesmo or eh_ultimo_admin:
                c_del.markdown('<div class="celula-centro">—</div>', unsafe_allow_html=True)
            else:
                if c_del.button("Excluir", key=f"del_admin_{usuario_da_linha}"):
                    remover_usuario_admin(usuario_da_linha)
                    st.toast(f"Administrador '{usuario_da_linha}' removido.")
                    st.rerun(scope="fragment")


# ------------------ RESULTADO DA CONSULTA: TABELA EDITÁVEL (SOLICITANTE) ------------------
# Pedido do usuário: na tela "Acompanhar meu chamado", o solicitante pode
# editar os dados do próprio chamado (E-mail, Empresa, Ferramenta,
# Severidade, Assunto, Descrição) e salvar com um botão — só não pode mexer
# em Protocolo, Solicitante e Status. Vale tanto no PC quanto no celular.
@st.fragment
def resultado_consulta_editavel(resultados):
    st.markdown(
        '<div class="titulo-resultado-consulta">📋 Resultado da Consulta:</div>',
        unsafe_allow_html=True,
    )

    col_widths = [1.1, 1.2, 1.6, 1.1, 1.2, 1.3, 1.3, 1.8, 1.3, 0.9]
    headers = ["Protocolo", "Solicitante", "E-mail", "Empresa", "Ferramenta", "Severidade", "Assunto", "Descrição", "Status", ""]
    opcoes_severidade = ["🟢 Baixa", "🟡 Média", "🟠 Alta", "🔴 Crítica"]

    with st.container(key="resultado_consulta_tabela"):
        cols_head = st.columns(col_widths)
        for col, h in zip(cols_head, headers):
            col.markdown(f'<div class="header-box">{h}</div>', unsafe_allow_html=True)

        for c in resultados:
            protocolo = c.get("protocolo", "-")
            (
                c_proto, c_nome, c_mail, c_emp, c_ferr,
                c_sev, c_ass, c_desc, c_stat, c_salvar,
            ) = st.columns(col_widths)

            c_proto.markdown(
                f'<div class="celula-protocolo"><span class="mobile-label">Protocolo:</span>{html.escape(str(protocolo))}</div>',
                unsafe_allow_html=True,
            )
            c_nome.markdown(
                f'<div class="celula-texto"><span class="mobile-label">Solicitante:</span>{html.escape(str(c.get("nome_solicitante", "-")))}</div>',
                unsafe_allow_html=True,
            )

            novo_email = c_mail.text_input(
                "E-mail", value=c.get("email_solicitante") or "", key=f"edit_email_{protocolo}",
                label_visibility="collapsed",
            )
            novo_emp = c_emp.text_input(
                "Empresa", value=c.get("empresa") or "", key=f"edit_empresa_{protocolo}",
                label_visibility="collapsed",
            )
            novo_ferr = c_ferr.text_input(
                "Ferramenta", value=c.get("ferramenta") or "", key=f"edit_ferramenta_{protocolo}",
                label_visibility="collapsed",
            )

            sev_atual = c.get("severidade") or opcoes_severidade[0]
            idx_sev = opcoes_severidade.index(sev_atual) if sev_atual in opcoes_severidade else 0
            nova_sev = c_sev.selectbox(
                "Severidade", opcoes_severidade, index=idx_sev, key=f"edit_severidade_{protocolo}",
                label_visibility="collapsed",
            )

            novo_ass = c_ass.text_input(
                "Assunto", value=c.get("assunto") or "", key=f"edit_assunto_{protocolo}",
                label_visibility="collapsed",
            )
            nova_desc = c_desc.text_area(
                "Descrição", value=c.get("descricao") or "", key=f"edit_descricao_{protocolo}",
                label_visibility="collapsed", height=68,
            )

            c_stat.markdown(
                f'<div class="celula-texto"><span class="mobile-label">Status:</span>'
                f'<span class="badge-status">{html.escape(str(c.get("status", "-")))}</span></div>',
                unsafe_allow_html=True,
            )

            if c_salvar.button("💾 Salvar", key=f"salvar_edicao_{protocolo}"):
                atualizar_chamado_solicitante(
                    protocolo, novo_email.strip(), novo_emp.strip(), novo_ferr.strip(),
                    nova_sev, novo_ass.strip(), nova_desc.strip(),
                )
                # Atualiza a lista em memória pra refletir a mudança na hora,
                # sem precisar pesquisar de novo
                c["email_solicitante"] = novo_email.strip()
                c["empresa"] = novo_emp.strip()
                c["ferramenta"] = novo_ferr.strip()
                c["severidade"] = nova_sev
                c["assunto"] = novo_ass.strip()
                c["descricao"] = nova_desc.strip()
                st.toast(f"Chamado {protocolo} atualizado!")
                st.rerun(scope="fragment")


# ------------------ NOTIFICAÇÃO DE SOLICITAÇÕES PENDENTES (ADMIN) ------------------
# Pedido do usuário: um balãozinho flutuante (estilo notificação do
# Messenger), visível só pra quem está logado como admin, mostrando quantos
# pedidos de "Criar conta" estão esperando aprovação. Clicando, abre a lista
# com Aprovar/Rejeitar pra cada um.
@st.fragment
def notificacao_pendentes_admin():
    pendentes = listar_solicitantes_pendentes()

    with st.container(key="notificacao_pendentes"):
        rotulo = f"🔔 {len(pendentes)}" if pendentes else "🔔"
        if st.button(rotulo, key="btn_toggle_pendentes"):
            st.session_state["mostrar_pendentes"] = not st.session_state["mostrar_pendentes"]
            st.rerun(scope="fragment")

    if st.session_state["mostrar_pendentes"]:
        with st.container(key="painel_pendentes"):
            st.markdown(
                '<div class="titulo-pendentes">📥 Solicitações de acesso pendentes</div>',
                unsafe_allow_html=True,
            )

            if not pendentes:
                st.caption("Nenhuma solicitação pendente no momento.")

            for p in pendentes:
                col_nome, col_email, col_aprovar, col_rejeitar = st.columns([1.4, 1.8, 0.7, 0.7])
                col_nome.markdown(
                    f'<div class="celula-texto">{html.escape(p.get("nome_usuario", "-"))}</div>',
                    unsafe_allow_html=True,
                )
                col_email.markdown(
                    f'<div class="celula-texto">{html.escape(p.get("email", "-"))}</div>',
                    unsafe_allow_html=True,
                )
                if col_aprovar.button("✅", key=f"aprovar_pendente_{p['nome_usuario']}"):
                    aprovar_solicitante(p["nome_usuario"])
                    st.toast(f"Conta de {p['nome_usuario']} aprovada!")
                    st.rerun(scope="fragment")
                if col_rejeitar.button("❌", key=f"rejeitar_pendente_{p['nome_usuario']}"):
                    rejeitar_solicitante(p["nome_usuario"])
                    st.toast(f"Pedido de {p['nome_usuario']} recusado.")
                    st.rerun(scope="fragment")


if st.session_state["usuario_logado"]:
    # Balãozinho flutuante de notificação (pedidos de "Criar conta"
    # pendentes) — visível em qualquer tela do admin, não só na de chamados.
    notificacao_pendentes_admin()

    if st.session_state["aba_admin"] == "empresa":
        painel_cadastros("empresa")
    elif st.session_state["aba_admin"] == "ferramenta":
        painel_cadastros("ferramenta")
    elif st.session_state["aba_admin"] == "usuarios":
        painel_usuarios_admin()
    else:
        painel_admin()

# ------------------ VISÃO PÚBLICA (SOLICITANTE LOGADO) ------------------
elif st.session_state.get("solicitante_logado"):
    # O robô/gif foi removido definitivamente (pedido do usuário) — a coluna
    # única "conteudo_publico" ocupa a largura toda no lugar do antigo par
    # col_robo/col_balao. Mantive o container(key=...) (em vez de um bloco
    # solto) só pra ter uma classe CSS (.st-key-conteudo_publico) pra aplicar
    # o mesmo espaçamento no topo que antes vinha da regra do robô.
    col_balao = st.container(key="conteudo_publico")
    with col_balao:
        if st.session_state["opcao_menu"] == "inicio":
            st.session_state["ultimo_protocolo"] = None
            st.session_state["etapa_abertura"] = 1
            # A saudação "Olá! Como posso te ajudar hoje?" foi removida daqui
            # pra não duplicar com a nova frase de boas-vindas exibida junto
            # da logo, acima (ver .logo-boas-vindas-box / _mostrar_boas_vindas_logo).

            with st.container(key="menu_home_botoes"):
                if st.button("📝 Abrir um novo chamado", key="btn_abrir_chamado"):
                    st.session_state["opcao_menu"] = "abrir"
                    st.rerun()

                if st.button("🔍 Acompanhar meu chamado", key="btn_acompanhar_chamado"):
                    st.session_state["opcao_menu"] = "acompanhar"
                    st.rerun()

                if st.button("⭐ Avaliar um atendimento", key="btn_avaliar_atendimento"):
                    st.session_state["opcao_menu"] = "avaliar"
                    st.rerun()

            # Link discreto de logout — agora que o solicitante faz login,
            # precisa de um jeito de sair (útil em computador compartilhado).
            with st.container(key="link_sair_solicitante"):
                if st.button("Sair", key="btn_sair_solicitante"):
                    st.session_state["solicitante_logado"] = None
                    st.session_state["opcao_menu"] = "inicio"
                    st.rerun()

        elif st.session_state["opcao_menu"] == "abrir":
            if st.session_state["etapa_abertura"] == 1:
                st.markdown(
                    '<div class="fala-titulo-sem-balao titulo-identificacao">👤 Identificação Inicial:</div>',
                    unsafe_allow_html=True,
                )

                if st.session_state["ultimo_protocolo"]:
                    st.markdown(
                        f"""
                        <div class="card-sucesso">
                            ✅ <b>Chamado registrado com sucesso!</b><br>
                            Seu Protocolo: <b style="font-size: 20px; color: #000000;">{st.session_state['ultimo_protocolo']}</b>
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
                    index=0,
                    key="select_empresa_etapa1"
                )

                nome = st.text_input(
                    "Digite seu Nome e Sobrenome",
                    value=st.session_state["temp_nome"],
                    placeholder="Ex: João Silva",
                    key="input_nome_solicitante"
                )

                with st.container(key="etapa1_botoes"):
                    if st.button("Avançar →", key="btn_avancar_etapa1"):
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

                    if st.button("← Voltar ao Menu", key="btn_voltar_etapa1"):
                        st.session_state["opcao_menu"] = "inicio"
                        st.rerun()

            elif st.session_state["etapa_abertura"] == 2:
                st.markdown(
                    '<div class="fala-titulo-sem-balao titulo-identificacao">📝 Detalhes do Chamado:</div>',
                    unsafe_allow_html=True,
                )

                with st.container(key="etapa2_campos"):
                    email = st.text_input("Seu E-mail", placeholder="exemplo@empresa.com")
                    ferramentas_cadastradas = listar_ferramentas()
                    ferramenta = st.selectbox(
                        "Escolha a ferramenta que necessita de ajuda",
                        ["Selecione..."] + ferramentas_cadastradas + ["Outro"],
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

                with st.container(key="etapa2_botoes"):
                    if st.button("🚀 Enviar Chamado", key="btn_enviar_chamado"):
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
                            # 1. Salva no banco
                            protocolo = salvar_chamado_supabase(
                                st.session_state["temp_nome"],
                                email,
                                st.session_state["temp_empresa"],
                                ferramenta,
                                assunto,
                                descricao,
                                severidade # <--- PASSANDO A SEVERIDADE
                            )
                            # 2. --- DISPARA O E-MAIL INICIAL ---
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

                    if st.button("← Voltar Etapa", key="btn_voltar_etapa2"):
                        st.session_state["etapa_abertura"] = 1
                        st.rerun()

        elif st.session_state["opcao_menu"] == "acompanhar":
            st.markdown(
                '<div class="fala-titulo-sem-balao titulo-identificacao">🔍 Consulte seu chamado:</div>',
                unsafe_allow_html=True,
            )
            termo_busca = st.text_input(
                "Digite o Protocolo ou E-mail",
                placeholder="Ex: F4-PXA6A4 ou seuemail@empresa.com",
                key="input_busca_protocolo"
            )

            # "Pesquisar" em cima e "Voltar ao Menu" embaixo, um debaixo do
            # outro (empilhados, com espaço entre eles via CSS — ver
            # .st-key-btn_voltar_menu_acompanhar). Já tentamos os dois lado a
            # lado (st.columns(2)), mas em telas mais estreitas o "Voltar ao
            # Menu" ficava cortado/vazando pra fora da tela.
            # O resultado da consulta (tabela) continua sendo renderizado MAIS
            # ABAIXO, fora dessa coluna estreita — veja o bloco "RESULTADO DA
            # CONSULTA (LARGURA TOTAL)" após o fechamento do container
            # conteudo_publico.
            if st.button("🔍 Pesquisar", key="btn_pesquisar_chamado"):
                termo_limpo = termo_busca.strip()
                # Guardado pra, se a busca não encontrar nada, decidir qual aviso
                # mostrar mais abaixo (formato incompleto x protocolo/e-mail
                # corretos mas sem chamado cadastrado) — precisa ficar salvo na
                # sessão porque esse trecho só roda no clique; o aviso em si é
                # exibido depois, lá na seção de resultado.
                st.session_state["ultimo_termo_busca"] = termo_limpo

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

            if st.button("← Voltar ao Menu", key="btn_voltar_menu_acompanhar"):
                st.session_state["opcao_menu"] = "inicio"
                if "resultado_busca" in st.session_state:
                    del st.session_state["resultado_busca"]
                st.rerun()

            # Dica pra rolar a tela: a tabela de resultado fica mais abaixo,
            # fora dessa coluna estreita (ver "RESULTADO DA CONSULTA (LARGURA
            # TOTAL)" mais abaixo) — sem essa pista, em zooms de tela mais
            # altos (100%+) o solicitante não vê a tabela e acha que a busca
            # não funcionou. Só aparece quando a busca realmente encontrou
            # pelo menos um chamado (não faz sentido pedir pra rolar se não
            # tem resultado nenhum pra ver).
            if st.session_state.get("resultado_busca"):
                st.markdown(
                    '<div class="dica-rolar-consulta"><span class="seta">⌄</span>'
                    "Role a tela para visualizar sua consulta</div>",
                    unsafe_allow_html=True,
                )

        elif st.session_state["opcao_menu"] == "avaliar":
            st.markdown(
                '<div class="fala-titulo-sem-balao titulo-identificacao">⭐ Deixe sua avaliação:</div>',
                unsafe_allow_html=True,
            )
            proto_input = st.text_input(
                "Número do Protocolo Concluído",
                placeholder="Ex: F4-PXA6A4",
                key="input_protocolo_avaliar"
            )

            if st.button("🔍 Buscar Chamado", key="btn_buscar_chamado_avaliar"):
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
                        <div class="card-avaliacao-info" style="margin-top: 15px;">
                            <div class="celula-protocolo">Protocolo: {chamado.get('protocolo')}</div>
                            <div class="celula-texto"><b>Solicitante:</b> {chamado.get('nome_solicitante')}</div>
                            <div class="celula-texto"><b>Assunto:</b> {chamado.get('assunto')}</div>
                            <div class="badge-status" style="margin-top: 8px;">{chamado.get('status')}</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                    st.markdown('<div class="titulo-como-foi">Como foi o seu atendimento?</div>', unsafe_allow_html=True)

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
                        placeholder="Conte-nos o que achou do atendimento ou o que podemos melhorar...",
                        key="textarea_comentario_avaliacao"
                    )

                    if st.button("🚀 Enviar Avaliação", key="btn_enviar_avaliacao"):
                        if supabase:
                            supabase.table("chamados").update({
                                "nota_avaliacao": nota_final,
                                "comentario_avaliacao": comentario.strip()
                            }).eq("protocolo", chamado.get("protocolo")).execute()

                            st.success("🎉 Muito obrigado! Sua avaliação foi enviada com sucesso.")
                            del st.session_state["chamado_para_avaliar"]

            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("← Voltar ao Menu", key="btn_voltar_menu_avaliar"):
                st.session_state["opcao_menu"] = "inicio"
                if "chamado_para_avaliar" in st.session_state:
                    del st.session_state["chamado_para_avaliar"]
                st.rerun()

    # ------------------ RESULTADO DA CONSULTA (LARGURA TOTAL) ------------------
    # Fica fora do container conteudo_publico, ocupando a largura toda da
    # tela — assim a tabela de 9 colunas não fica espremida.
    if st.session_state["opcao_menu"] == "acompanhar":
        if "resultado_busca" in st.session_state and st.session_state["resultado_busca"] is not None:
            resultados = st.session_state["resultado_busca"]

            if not resultados:
                # Dois avisos diferentes, dependendo do que a pessoa digitou:
                # se não parece nem um e-mail nem um protocolo completo (ex:
                # esqueceu o #, o "F4" ou o traço), ensina o formato certo.
                # Se digitou algo no formato certo (ou um e-mail) e mesmo
                # assim não achou nada, aí sim é "não encontrado de verdade".
                termo_pesquisado = st.session_state.get("ultimo_termo_busca", "").strip()
                eh_email = "@" in termo_pesquisado
                eh_protocolo_completo = bool(
                    re.match(r"^#?F4-[A-Za-z0-9]{6}$", termo_pesquisado, re.IGNORECASE)
                )

                with st.container(key="aviso_busca_chamado"):
                    if termo_pesquisado and not eh_email and not eh_protocolo_completo:
                        st.warning(
                            "⚠️ Digite o número do protocolo completo, incluindo o símbolo "
                            "**#**, a sigla **F4** e o traço. Exemplo: **#F4-AXWR25**"
                        )
                    else:
                        st.error(
                            "❌ Seu chamado não foi encontrado. Confirme se o protocolo "
                            "ou e-mail digitado está correto."
                        )
            else:
                resultado_consulta_editavel(resultados)
        # O botão "Voltar ao Menu" dessa etapa foi movido pra cima, ao lado do
        # "Pesquisar" (ver col_pesquisar/col_voltar_acompanhar) — não fica
        # mais aqui embaixo, sozinho e longe do resto.

# ------------------ TELA DE LOGIN (SOLICITANTE) — NOVA TELA INICIAL ------------------
# Pedido do usuário: agora TODO MUNDO (administrador e solicitante comum)
# passa por login antes de qualquer coisa. Enquanto ninguém fez login (nem
# como admin, nem como solicitante), é essa tela que aparece — no lugar dos
# 3 botões de sempre entram os campos de Usuário/Senha/Entrar, e um link
# discreto pra "Criar conta" (e outro pra "Esqueci minha senha").
else:
    st.markdown(
        f'<div class="logo-boas-vindas-box">'
        f'<img src="{logo_boas_vindas_src}">'
        f'<div class="boas-vindas-texto">Seja bem-vindo ao Helpdesk.</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if not st.session_state["mostrar_criar_conta"] and not st.session_state["mostrar_esqueci_senha_solicitante"]:
        # ---- LOGIN (padrão) ----
        with st.container(key="tela_login_solicitante"):
            usuario_login = st.text_input(
                "Usuário", placeholder="seu.usuario", key="input_usuario_login_solicitante"
            )
            senha_login = st.text_input(
                "Senha", type="password", key="input_senha_login_solicitante"
            )

            with st.container(key="login_solicitante_botoes"):
                if st.button("Entrar", key="btn_entrar_solicitante"):
                    usuario_norm = (usuario_login or "").strip().lower()
                    if not usuario_norm or not senha_login:
                        st.warning("⚠️ Preencha usuário e senha.")
                    elif verificar_login(usuario_norm, senha_login):
                        # É um administrador — mesma verificação de sempre.
                        st.session_state["usuario_logado"] = usuario_norm
                        st.rerun()
                    else:
                        resultado_login = verificar_login_solicitante(usuario_norm, senha_login)
                        if resultado_login == "ok":
                            registro_sol = buscar_solicitante(usuario_norm)
                            st.session_state["solicitante_logado"] = {
                                "nome_usuario": usuario_norm,
                                "email": registro_sol["email"] if registro_sol else "",
                            }
                            st.rerun()
                        elif resultado_login == "pendente":
                            st.warning(
                                "⏳ Sua conta ainda está aguardando aprovação do administrador."
                            )
                        else:
                            st.error("Usuário ou senha incorretos.")

            with st.container(key="links_login_solicitante"):
                col_link_criar, col_link_esqueci = st.columns(2)
                with col_link_criar:
                    if st.button("Criar conta", key="btn_toggle_criar_conta"):
                        st.session_state["mostrar_criar_conta"] = True
                        st.rerun()
                with col_link_esqueci:
                    if st.button("Esqueci minha senha", key="btn_toggle_esqueci_senha_solicitante"):
                        st.session_state["mostrar_esqueci_senha_solicitante"] = True
                        st.rerun()

    elif st.session_state["mostrar_criar_conta"]:
        # ---- CRIAR CONTA ----
        with st.container(key="tela_login_solicitante"):
            st.markdown(
                '<div class="fala-titulo-sem-balao titulo-identificacao">📝 Criar conta:</div>',
                unsafe_allow_html=True,
            )

            novo_usuario_sol = st.text_input(
                "Nome de usuário",
                placeholder="ex: felipe.rodrigues",
                key="input_novo_usuario_solicitante",
            )
            st.caption("Só letras minúsculas, sem espaço. Nome composto: separe com ponto.")
            novo_email_sol = st.text_input(
                "E-mail corporativo",
                placeholder="voce@suaempresa.com",
                key="input_novo_email_solicitante",
            )
            nova_senha_sol_criar = st.text_input(
                "Senha desejada", type="password", key="input_nova_senha_solicitante"
            )
            st.caption("Precisa ter pelo menos 1 caractere especial (número é opcional).")

            with st.container(key="login_solicitante_botoes"):
                if st.button("✅ Criar usuário", key="btn_criar_usuario_solicitante"):
                    resultado_criacao = criar_solicitacao_conta(
                        novo_usuario_sol, novo_email_sol, nova_senha_sol_criar
                    )
                    if resultado_criacao["ok"]:
                        st.success(
                            "Solicitação enviada! Assim que um administrador aprovar, "
                            "você já pode entrar normalmente."
                        )
                    else:
                        st.warning(f"⚠️ {resultado_criacao['erro']}")

                if st.button("← Voltar", key="btn_voltar_criar_conta"):
                    st.session_state["mostrar_criar_conta"] = False
                    st.rerun()

    elif st.session_state["mostrar_esqueci_senha_solicitante"]:
        # ---- ESQUECI MINHA SENHA (via código enviado por e-mail) ----
        with st.container(key="tela_login_solicitante"):
            st.markdown(
                '<div class="fala-titulo-sem-balao titulo-identificacao">🔑 Esqueci minha senha:</div>',
                unsafe_allow_html=True,
            )

            if not st.session_state["codigo_senha_solicitante_enviado"]:
                usuario_recuperar = st.text_input(
                    "Seu nome de usuário", key="input_usuario_recuperar_senha"
                )

                with st.container(key="login_solicitante_botoes"):
                    if st.button("📧 Enviar código por e-mail", key="btn_enviar_codigo_solicitante"):
                        usuario_recuperar_norm = (usuario_recuperar or "").strip().lower()
                        registro_recuperar = buscar_solicitante(usuario_recuperar_norm)
                        if not registro_recuperar:
                            st.error("Não encontramos essa conta.")
                        else:
                            codigo_sol = "".join(random.choices(string.digits, k=6))
                            if enviar_email_codigo_senha(
                                registro_recuperar["email"], usuario_recuperar_norm, codigo_sol
                            ):
                                st.session_state["codigo_senha_solicitante_valor"] = codigo_sol
                                st.session_state["codigo_senha_solicitante_usuario"] = usuario_recuperar_norm
                                st.session_state["codigo_senha_solicitante_gerado_em"] = datetime.now(timezone.utc)
                                st.session_state["codigo_senha_solicitante_enviado"] = True
                                st.success(f"Código enviado para {registro_recuperar['email']}!")
                                st.rerun()
                            else:
                                st.error("Não foi possível enviar o e-mail. Tente novamente mais tarde.")

                    if st.button("← Voltar", key="btn_voltar_esqueci_senha_1"):
                        st.session_state["mostrar_esqueci_senha_solicitante"] = False
                        st.rerun()
            else:
                codigo_digitado_sol = st.text_input(
                    "Código recebido por e-mail", key="input_codigo_solicitante"
                )
                nova_senha_recuperar = st.text_input(
                    "Nova senha", type="password", key="input_nova_senha_recuperar"
                )
                confirmar_nova_senha_recuperar = st.text_input(
                    "Confirmar nova senha", type="password", key="input_confirmar_nova_senha_recuperar"
                )

                with st.container(key="login_solicitante_botoes"):
                    if st.button("💾 Confirmar e trocar senha", key="btn_confirmar_nova_senha_solicitante"):
                        codigo_valido_sol = st.session_state.get("codigo_senha_solicitante_valor")
                        gerado_em_sol = st.session_state.get("codigo_senha_solicitante_gerado_em")
                        expirado_sol = gerado_em_sol and (
                            datetime.now(timezone.utc) - gerado_em_sol
                        ).total_seconds() > 600

                        if not validar_formato_senha_solicitante(nova_senha_recuperar):
                            st.warning("A nova senha precisa ter pelo menos 1 caractere especial.")
                        elif expirado_sol:
                            st.error("Código expirado. Solicite um novo.")
                            st.session_state["codigo_senha_solicitante_enviado"] = False
                        elif codigo_digitado_sol.strip() != codigo_valido_sol:
                            st.error("Código incorreto.")
                        elif nova_senha_recuperar != confirmar_nova_senha_recuperar:
                            st.warning("A confirmação não confere com a nova senha.")
                        else:
                            atualizar_senha_solicitante(
                                st.session_state["codigo_senha_solicitante_usuario"], nova_senha_recuperar
                            )
                            st.session_state["mostrar_esqueci_senha_solicitante"] = False
                            st.session_state["codigo_senha_solicitante_enviado"] = False
                            st.session_state.pop("codigo_senha_solicitante_valor", None)
                            st.session_state.pop("codigo_senha_solicitante_gerado_em", None)
                            st.session_state.pop("codigo_senha_solicitante_usuario", None)
                            st.success("Senha alterada! Já pode entrar com a nova senha.")
                            st.rerun()

                    if st.button("🔁 Reenviar código", key="btn_reenviar_codigo_solicitante"):
                        st.session_state["codigo_senha_solicitante_enviado"] = False
                        st.rerun()