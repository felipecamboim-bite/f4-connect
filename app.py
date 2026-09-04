import random
import string
import hashlib
import html
import re
import unicodedata
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta, timezone
from supabase import create_client, Client
import gspread
from google.oauth2.service_account import Credentials
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# 1. Configuração da página
st.set_page_config(
    page_title="HelpDesk",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="expanded",
)

# Pedido do usuário: no celular com o modo escuro do sistema ativado, o
# navegador (esse recurso existe em vários navegadores de celular, tipo o
# "modo escuro forçado" do Chrome no Android) tentava "ajudar" escurecendo
# sozinho as telas com fundo branco (login, menu inicial) — só que a logo
# tem o texto em preto, pensado pra fundo claro, e sumia em cima do fundo
# escurecido à força. O CSS (color-scheme / background-color no html,body)
# sozinho não é suficiente pra desligar esse recurso em todos os
# navegadores; a forma confiável é essa <meta name="color-scheme"> dentro
# do <head> de verdade da página — e como st.markdown não roda <script>,
# isso só é possível via um componente (que roda dentro de um iframe, mas
# com acesso à página principal por estar na mesma origem).
components.html(
    """
    <script>
    (function() {
        try {
            var doc = window.parent.document;
            if (!doc.querySelector('meta[name="color-scheme"]')) {
                var meta = doc.createElement('meta');
                meta.name = 'color-scheme';
                meta.content = 'light only';
                doc.head.appendChild(meta);
            }
        } catch (e) {}
    })();
    </script>
    """,
    height=0,
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
                "created_at": datetime.now(timezone.utc).isoformat(),
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

def _normalizar_texto_busca(texto):
    """
    Remove acentos, espaços duplicados e deixa em minúsculo, pra comparar
    nomes/situação de forma tolerante a diferenças de digitação.
    """
    if not texto:
        return ""
    texto = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode("ascii")
    return " ".join(texto.strip().lower().split())


# ID da planilha "validacao_cadastro_chamados" (cópia com nome_completo +
# situacao, alimentada via IMPORTRANGE a partir da planilha real do RH).
# Essa planilha-cópia é a única que o app enxerga; a do RH nunca é tocada.
_SHEET_ID_VALIDACAO_CADASTRO = "1h02pt8nufDXK69_WTyBMHvjWEkzikG-69DU8OxM4oAI"


def _obter_cliente_sheets():
    """Autentica com a conta de serviço (somente leitura) configurada nos secrets."""
    try:
        escopos = ["https://www.googleapis.com/auth/spreadsheets.readonly", "https://www.googleapis.com/auth/drive.readonly"]
        credenciais = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"], scopes=escopos
        )
        return gspread.authorize(credenciais)
    except Exception:
        return None


def listar_colaboradores_rh():
    """
    Lê a planilha-cópia (nome_completo + situacao), usada só pra validar o
    nome completo na criação de conta. Nunca lê nem escreve na planilha
    original do RH — só nessa cópia, e só em modo leitura.
    """
    cliente = _obter_cliente_sheets()
    if not cliente:
        return []
    try:
        aba = cliente.open_by_key(_SHEET_ID_VALIDACAO_CADASTRO).sheet1
        registros = aba.get_all_records()
        return [
            {"nome_completo": r.get("nome_completo"), "situacao": r.get("situacao")}
            for r in registros
        ]
    except Exception:
        return []


def verificar_situacao_colaborador(nome_completo):
    """
    Confere o nome completo informado contra a lista de colaboradores do RH.
    Retorna "ativo", "desligado" ou None (nome não encontrado na lista).
    """
    alvo = _normalizar_texto_busca(nome_completo)
    if not alvo:
        return None
    for colaborador in listar_colaboradores_rh():
        if _normalizar_texto_busca(colaborador.get("nome_completo")) == alvo:
            situacao = _normalizar_texto_busca(colaborador.get("situacao"))
            return "ativo" if situacao == "ativo" else "desligado"
    return None


def criar_solicitacao_conta(nome_completo, nome_usuario, email, senha):
    nome_completo_norm = (nome_completo or "").strip()
    nome_norm = (nome_usuario or "").strip().lower()
    email_norm = (email or "").strip()

    if not nome_completo_norm:
        return {"ok": False, "erro": "Digite seu nome completo."}
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

    situacao_rh = verificar_situacao_colaborador(nome_completo_norm)

    if situacao_rh == "desligado":
        return {
            "ok": False,
            "erro": "Não foi possível concluir o cadastro. Entre em contato com o RH ou "
                    "com o administrador do sistema.",
        }

    status_inicial = "aprovado" if situacao_rh == "ativo" else "pendente"

    if supabase:
        supabase.table("solicitantes").insert(
            {
                "nome_completo": nome_completo_norm,
                "nome_usuario": nome_norm,
                "email": email_norm,
                "senha": hash_senha(senha),
                "status": status_inicial,
            }
        ).execute()

    if status_inicial == "aprovado":
        enviar_email_conta_solicitante_aprovada(email_norm, nome_norm)

    return {"ok": True, "aprovado_automaticamente": status_inicial == "aprovado"}

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

# Bucket do Supabase Storage onde ficam os anexos (imagem/PDF) que o
# solicitante manda junto do chamado. Precisa existir no projeto Supabase
# com esse nome exato (ver instruções de configuração).
NOME_BUCKET_ANEXOS = "anexos-chamados"

# Ícone de clipe (anexo) em SVG — traço fino, sem fundo/caixa, e com a cor
# controlada via CSS (stroke="currentColor") em vez de um emoji (que vem
# colorido "de fábrica" e não dá pra deixar branco/apagado por CSS).
ICONE_CLIPS_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="17" height="17" viewBox="0 0 24 24" '
    'fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" '
    'stroke-linejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 '
    '5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"></path></svg>'
)

# Paleta do painel de Insights (BI): usa os verdes da identidade do site
# (mesmo tom dos botões/menu) em vez de azul/ciano, pra não destoar do
# resto do sistema. "Cancelado" fica num cinza neutro de propósito — não
# é um resultado "positivo" pra ganhar um tom de verde.
CORES_STATUS_INSIGHTS = {
    "Aguardando atendimento": "#C9DF8A",
    "Em análise": "#8BC34A",
    "Em atendimento": "#4C7A1E",
    "Concluído": "#1D5902",
    "Cancelado": "#8A8A8A",
    "Encerrado pelo solicitante": "#72A703",
}

# Pedido do usuário: nenhum emoji nas telas do solicitante nem nos rótulos
# que ele escolhe (por isso as opções de severidade abaixo não têm mais a
# bolinha colorida embutida no valor). A bolinha continua existindo, mas só
# no PAINEL DO ADMINISTRADOR, aplicada aqui em cima do texto puro — funciona
# tanto pra chamados novos (severidade salva sem emoji) quanto pra chamados
# antigos (que já foram salvos com o emoji embutido no valor, ex: "🟢 Baixa").
_SEVERIDADE_INFO = [
    ("crítica", "🔴", "Crítica"),
    ("critica", "🔴", "Crítica"),
    ("alta", "🟠", "Alta"),
    ("média", "🟡", "Média"),
    ("media", "🟡", "Média"),
    ("baixa", "🟢", "Baixa"),
]

def normalizar_severidade(severidade):
    """Extrai só o nome da severidade (sem emoji), pra bater com as opções
    do selectbox mesmo em chamados antigos salvos com o emoji embutido."""
    if not severidade:
        return None
    texto_lower = severidade.lower()
    for chave, _emoji, rotulo in _SEVERIDADE_INFO:
        if chave in texto_lower:
            return rotulo
    return severidade

def formatar_severidade_admin(severidade):
    """Severidade com a bolinha colorida — só usado na tabela do painel do
    administrador, nunca nas telas do solicitante."""
    if not severidade:
        return "-"
    texto_lower = severidade.lower()
    for chave, emoji, rotulo in _SEVERIDADE_INFO:
        if chave in texto_lower:
            return f"{emoji} {rotulo}"
    return severidade

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
            {
                "nome": nome,
                "criado_por": criado_por,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
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
            {
                "nome": nome,
                "criado_por": criado_por,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ).execute()

def remover_ferramenta(nome):
    if supabase:
        supabase.table("ferramentas_chamados").delete().eq("nome", nome).execute()

def listar_unidades():
    """Retorna apenas os nomes, usado para popular o select de unidade
    (só aparece pro solicitante quando a empresa é a ClickLog Transportes)."""
    if supabase:
        res = supabase.table("unidades_chamados").select("nome").order("nome").execute()
        return [item["nome"] for item in res.data] if res.data else []
    return []

def listar_unidades_detalhado():
    """Retorna nome, quem cadastrou e a data, usado no painel administrativo."""
    if supabase:
        res = (
            supabase.table("unidades_chamados")
            .select("nome, criado_por, created_at")
            .order("created_at", desc=True)
            .execute()
        )
        return res.data if res.data else []
    return []

def adicionar_unidade(nome, criado_por):
    if supabase:
        supabase.table("unidades_chamados").insert(
            {
                "nome": nome,
                "criado_por": criado_por,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ).execute()

def remover_unidade(nome):
    if supabase:
        supabase.table("unidades_chamados").delete().eq("nome", nome).execute()

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
                <h2 style="color: #007aff; text-align: center; margin-bottom: 5px;">F4 Connect - Help Desk</h2>
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
                <h2 style="color: #007aff; text-align: center; margin-bottom: 5px;">F4 Connect - Help Desk</h2>
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
                <h2 style="color: #007aff; text-align: center; margin-bottom: 5px;">F4 Connect - Help Desk</h2>
                <p style="text-align: center; color: #666; font-size: 14px; margin-top: 0;">Acesso ao painel administrativo</p>
                <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">

                <p>Olá!</p>
                <p>Você foi cadastrado como administrador do F4 Connect HelpDesk. Seus dados de acesso:</p>

                <div style="background-color: #f8fafc; border-left: 4px solid #007aff; padding: 15px; margin: 20px 0; border-radius: 4px;">
                    <p style="margin: 6px 0;"><b>Usuário:</b> {usuario}</p>
                    <p style="margin: 6px 0;"><b>Senha temporária:</b> <span style="color: #007aff; font-weight: bold;">{senha_temporaria}</span></p>
                </div>

                <p>Recomendamos trocar essa senha assim que fizer login, usando a opção "Alterar senha" no painel administrativo.</p>
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
                <h2 style="color: #007aff; text-align: center; margin-bottom: 5px;">F4 Helpdesk</h2>
                <p style="text-align: center; color: #666; font-size: 14px; margin-top: 0;">Acesso liberado</p>
                <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">

                <p>Olá!</p>
                <p>Sua conta foi criada no F4 HelpDesk. Seu usuário de acesso:</p>

                <div style="background-color: #f8fafc; border-left: 4px solid #007aff; padding: 15px; margin: 20px 0; border-radius: 4px;">
                    <p style="margin: 6px 0;"><b>Usuário:</b> {nome_usuario}</p>
                </div>

                <p>Já pode entrar com o usuário e a senha que você cadastrou.</p>
                <br>
                <p style="margin-bottom: 0;">Atenciosamente,</p>
                <p style="margin-top: 2px;"><b>Equipe de Suporte F4 Helpdesk</b></p>

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

def salvar_chamado_supabase(nome, email, empresa, ferramenta, assunto, descricao, severidade, unidade=None, telefone_contato=None):
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
        "unidade": unidade, # <--- só preenchido quando empresa = ClickLog Transportes
        "telefone_contato": telefone_contato, # <--- só preenchido quando a unidade é filial
        "status": "Aguardando atendimento"
    }
    if supabase:
        supabase.table("chamados").insert(dados).execute()
    return protocolo

def atualizar_atendente_chamado(protocolo, novo_atendente):
    if supabase:
        supabase.table("chamados").update({"atendente": novo_atendente}).eq("protocolo", protocolo).execute()

def enviar_anexo_chamado(protocolo, arquivo):
    """Sobe o arquivo (imagem ou PDF) anexado na abertura do chamado pro
    Supabase Storage e devolve a URL pública dele. Retorna None se não
    tiver Supabase configurado, não tiver arquivo, ou o upload falhar (o
    chamado já foi salvo antes disso, então um anexo com problema não
    impede a abertura do chamado — só fica sem o anexo)."""
    if not supabase or not arquivo:
        return None
    try:
        extensao = arquivo.name.split(".")[-1].lower() if "." in arquivo.name else "bin"
        # O protocolo vem com "#" na frente (ex: "#F4-AB12CD") — esse
        # caractere não é seguro num nome de arquivo/URL, então tira ele
        # (e qualquer "/") antes de usar como nome do arquivo no Storage.
        protocolo_seguro = protocolo.lstrip("#").replace("/", "-")
        caminho = f"{protocolo_seguro}.{extensao}"
        supabase.storage.from_(NOME_BUCKET_ANEXOS).upload(
            path=caminho,
            file=arquivo.getvalue(),
            file_options={"content-type": arquivo.type or "application/octet-stream"},
        )
        resultado = supabase.storage.from_(NOME_BUCKET_ANEXOS).get_public_url(caminho)
        # Versões diferentes do cliente supabase-py devolvem a URL de jeitos
        # diferentes (string direta, ou um dicionário) — cobre os dois casos.
        if isinstance(resultado, dict):
            return resultado.get("publicURL") or resultado.get("public_url") or resultado.get("data", {}).get("publicUrl")
        return resultado
    except Exception as erro:
        # Não quebra a abertura do chamado (que já foi salva) — só deixa
        # registrado no log do Streamlit Cloud pra dar pra investigar.
        print(f"[anexo] Falha ao enviar anexo do chamado {protocolo}: {erro}")
        return None

def atualizar_anexo_chamado(protocolo, anexo_url):
    if supabase and anexo_url:
        supabase.table("chamados").update({"anexo_url": anexo_url}).eq("protocolo", protocolo).execute()

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

# Mesma logo, só que com o texto "F4 HELPDESK"/"Conectando as pontas" em
# branco (o arquivo original tem o texto em preto, pensado pra fundo
# branco) — usada só na sidebar do administrador, que tem fundo escuro.
# Embutida direto em base64 (em vez de um link) pra não depender de
# nenhum host externo.
logo_sidebar_admin_src = "data:image/png;base64,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"

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

# Unidade (só usada quando a empresa selecionada é a ClickLog Transportes) e
# telefone de contato (só quando a unidade escolhida é uma filial, não a matriz)
if "temp_unidade" not in st.session_state:
    st.session_state["temp_unidade"] = None

if "temp_telefone" not in st.session_state:
    st.session_state["temp_telefone"] = None

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
# o painel do administrador (logado) continua na cor #1A1A1A.
_tela_publica = not st.session_state["usuario_logado"]
_cor_fundo_app = "#FFFFFF" if _tela_publica else "#1A1A1A"
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
        html {{
            /* Impede que o celular/navegador aplique o modo escuro do
             sistema por conta própria (o que deixava o fundo branco das
             telas de login/menu virando preto e escondendo a logo, que
             tem o texto em preto pensado pra fundo claro). O app já
             controla sozinho quando é claro (público) ou escuro (admin),
             então o navegador não precisa "ajudar" com isso. */
            color-scheme: light !important;
        }}

        html, body {{
            margin: 0 !important;
            overflow-x: hidden !important;
            scroll-behavior: auto !important;
            background-color: {_cor_fundo_app} !important;
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

        /* Logo do F4 Connect HelpDesk no topo da sidebar — só aparece
           logado como administrador (Felipe/Rafael), ver Python logo
           abaixo do "Logado como". */
        .logo-sidebar-admin {{
            text-align: center !important;
            margin-bottom: 34px !important;
        }}

        .logo-sidebar-admin img {{
            max-width: 150px !important;
            width: 75% !important;
            height: auto !important;
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

        /* Avisos (st.warning/st.error/st.success) na tela de login/criar
           conta/esqueci senha: mesmo problema já corrigido em
           .st-key-conteudo_publico — o texto vem branco (herdado do tema
           escuro padrão do app), invisível em cima do fundo claro de cada
           aviso. Caixa mais estreita/centralizada e uma cor escura por tipo
           (aviso, erro, sucesso), legível em cada fundo. */
        .st-key-tela_login_solicitante .stAlert {{
            max-width: 380px !important;
            margin-left: auto !important;
            margin-right: auto !important;
            padding: 10px 14px !important;
        }}

        .st-key-tela_login_solicitante [data-testid="stAlertContentWarning"],
        .st-key-tela_login_solicitante [data-testid="stAlertContentWarning"] p,
        .st-key-tela_login_solicitante [data-testid="stAlertContentWarning"] div {{
            color: #664d03 !important;
            font-size: 14px !important;
        }}

        .st-key-tela_login_solicitante [data-testid="stAlertContentError"],
        .st-key-tela_login_solicitante [data-testid="stAlertContentError"] p,
        .st-key-tela_login_solicitante [data-testid="stAlertContentError"] div {{
            color: #842029 !important;
            font-size: 14px !important;
        }}

        .st-key-tela_login_solicitante [data-testid="stAlertContentSuccess"],
        .st-key-tela_login_solicitante [data-testid="stAlertContentSuccess"] p,
        .st-key-tela_login_solicitante [data-testid="stAlertContentSuccess"] div {{
            color: #0f5132 !important;
            font-size: 14px !important;
        }}

        .st-key-tela_login_solicitante [data-testid="stAlertContentInfo"],
        .st-key-tela_login_solicitante [data-testid="stAlertContentInfo"] p,
        .st-key-tela_login_solicitante [data-testid="stAlertContentInfo"] div {{
            color: #055160 !important;
            font-size: 14px !important;
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
            background-color: #1A1A1A !important;
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

        /* Aprovar/Rejeitar: só o emoji (✓ verde / X vermelho), mas com uma
           "cara" de botão pequeno e discreto — quadradinho arredondado, em
           vez de ficar largo com a palavra escrita (que ficava desproporcional
           nessa coluna estreita). */
        .st-key-painel_pendentes .stButton > button {{
            width: 32px !important;
            height: 32px !important;
            max-width: 32px !important;
            min-height: 32px !important;
            padding: 0 !important;
            margin: 2px auto !important;
            background-color: #2b2d31 !important;
            border: 1px solid rgba(255, 255, 255, 0.15) !important;
            border-radius: 8px !important;
            box-shadow: 0 2px 6px rgba(0, 0, 0, 0.35) !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
        }}

        .st-key-painel_pendentes .stButton > button p {{
            font-size: 14px !important;
            line-height: 1 !important;
            margin: 0 !important;
        }}

        .st-key-painel_pendentes .stButton > button:hover {{
            background-color: #3a3d42 !important;
            border-color: rgba(255, 255, 255, 0.3) !important;
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

        /* ========================================================= */
        /* PAINEL DE INSIGHTS (BI): usa os mesmos verdes da identidade do  */
        /* site (logo/menu/botões), em vez do azul/ciano das tabelas do   */
        /* admin — pedido do usuário pra não virar um "carnaval de cores" */
        /* misturado com o visual branco/verde do resto do sistema.       */
        /* Pedido do usuário: sem "tela flutuante" própria por trás de     */
        /* tudo — o fundo fica preto igual ao resto do painel do admin,   */
        /* só os cartõezinhos de métrica (mais abaixo) continuam com      */
        /* destaque, que foi o que ele disse que gostou.                  */
        /* ========================================================= */
        .st-key-painel_insights {{
            background-color: transparent !important;
            border: none !important;
            padding: 16px 0 !important;
        }}

        /* Letras (títulos de seção, tipo "Chamados por status") em branco;
           só os NÚMEROS (valores das métricas, mais abaixo) ficam verdes */
        .subtitulo-insights {{
            color: #FFFFFF !important;
            font-family: 'Inter', sans-serif !important;
            font-weight: 800 !important;
            font-size: 14px !important;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin: 18px 0 10px 0 !important;
        }}

        /* Pedido do usuário: sem fundo nem borda nos cartõezinhos de
           métrica (nem verde, nem cinza) — só o texto/número, centralizado */
        .st-key-painel_insights [data-testid="stMetric"] {{
            background-color: transparent !important;
            border: none !important;
            padding: 10px 6px !important;
            text-align: center !important;
        }}

        .st-key-painel_insights [data-testid="stMetric"] > div {{
            align-items: center !important;
            justify-content: center !important;
        }}

        /* Centraliza de verdade tanto o texto quanto o número, mesmo
           quando o rótulo é curto (ex: "Total", "Cancelado") — sem forçar
           display:flex + text-align nos dois níveis (o de fora e o <p>/
           <div> de dentro), rótulos curtos ficavam grudados na esquerda
           enquanto o número (que já era flex) ficava centralizado */
        .st-key-painel_insights [data-testid="stMetricLabel"],
        .st-key-painel_insights [data-testid="stMetricValue"] {{
            display: flex !important;
            justify-content: center !important;
            align-items: center !important;
            width: 100% !important;
            text-align: center !important;
        }}

        .st-key-painel_insights [data-testid="stMetricLabel"] p,
        .st-key-painel_insights [data-testid="stMetricLabel"] div,
        .st-key-painel_insights [data-testid="stMetricLabel"] span {{
            text-align: center !important;
            width: 100% !important;
        }}

        .st-key-painel_insights [data-testid="stMetricLabel"] {{
            color: #FFFFFF !important;
            font-size: 12px !important;
        }}

        .st-key-painel_insights [data-testid="stMetricValue"] {{
            color: #9BCB2E !important;
        }}

        /* Filtro de período: mais discreto (não ocupa mais a largura toda),
           pra sobrar espaço do lado quando outros filtros forem adicionados
           — ver largura da coluna estreita lá no Python (painel_insights) */
        .st-key-painel_insights .stSelectbox div[data-baseweb="select"] {{
            background-color: rgba(29, 89, 2, 0.35) !important;
            border: 1px solid rgba(114, 167, 3, 0.5) !important;
            border-radius: 8px !important;
        }}

        .st-key-painel_insights .stSelectbox div[data-baseweb] * {{
            color: #FFFFFF !important;
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

        /* Rótulo "Telefone para contato" acima do par DDD/Número (etapa 1
           da abertura de chamado, só aparece pra unidade = filial) — mesmo
           estilo dos rótulos padrão dos campos, pra ficar consistente. */
        .rotulo-telefone-unidade {{
            font-size: 15px !important;
            font-weight: 600 !important;
            color: {_cor_label_campo} !important;
            font-family: 'Inter', sans-serif !important;
            margin: 4px 0 2px 0 !important;
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
        .st-key-conteudo_publico .stSelectbox [role="group"],
        .st-key-conteudo_publico .stSelectbox input[role="combobox"] {{
            background-color: #F1F1EA !important;
            color: #24261F !important;
            border: none !important;
            border-color: transparent !important;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15) !important;
            outline: none !important;
        }}

        /* Texto/ícone de dentro do selectbox (ex: "Selecione...", a setinha) —
           herdavam branco do tema escuro padrão do Streamlit e ficavam
           invisíveis no fundo claro novo. Essa versão do Streamlit usa um
           componente "react-aria" (input[role="combobox"] de verdade, mais
           um <button aria-label="Open"> pro ícone), não o antigo
           data-baseweb — por isso o seletor é por esses atributos. */
        .st-key-conteudo_publico .stSelectbox input[role="combobox"]::placeholder {{
            color: #24261F !important;
            opacity: 1 !important;
        }}

        .st-key-conteudo_publico .stSelectbox button[aria-label="Open"],
        .st-key-conteudo_publico .stSelectbox button[aria-label="Open"] svg {{
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
        .st-key-conteudo_publico .stSelectbox [role="group"]:focus-within,
        .st-key-conteudo_publico .stSelectbox input[role="combobox"]:focus {{
            border: none !important;
            border-color: transparent !important;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15) !important;
            outline: none !important;
        }}

        /* Menu que abre com as opções do campo (Empresa, Ferramenta, etc.):
           esse menu "flutua" separado do campo no HTML (é renderizado à
           parte pelo componente react-aria), então não dá pra alcançá-lo só
           escopando por .st-key-conteudo_publico — por isso essa regra não
           tem esse escopo, vale pro app inteiro. Usa os nomes de classe
           padrão do react-aria-components (biblioteca usada por trás do
           campo, confirmado pelo "react-aria-ComboBox" visto no
           Inspecionar). Se não bater exatamente, é só ajustar depois. */
        .react-aria-Popover {{
            background-color: #F1F1EA !important;
            border: none !important;
            box-shadow: 0 8px 20px rgba(0, 0, 0, 0.25) !important;
        }}

        .react-aria-ListBox,
        .react-aria-ListBoxItem,
        .react-aria-Option {{
            background-color: #F1F1EA !important;
            color: #24261F !important;
        }}

        .react-aria-ListBoxItem[data-focused="true"],
        .react-aria-ListBoxItem[data-hovered="true"],
        .react-aria-ListBoxItem[aria-selected="true"],
        .react-aria-Option[data-focused="true"],
        .react-aria-Option[data-hovered="true"],
        .react-aria-Option[aria-selected="true"] {{
            background-color: rgba(0, 0, 0, 0.08) !important;
            color: #24261F !important;
        }}

        /* Campos "Qual empresa você faz parte?" e "Digite seu Nome e Sobrenome":
           estavam esticando quase até a borda da tela — largura mais contida */
        .st-key-select_empresa_etapa1,
        .st-key-select_empresa_etapa1 div[data-baseweb="select"],
        .st-key-input_nome_solicitante,
        .st-key-input_nome_solicitante input {{
            max-width: 380px !important;
        }}

        /* Campos novos da etapa 1 (Selecionar Unidade + Telefone para
           contato): mesma largura máxima dos campos "Empresa"/"Nome" acima,
           pra não ficarem gigantes/desproporcionais no celular. */
        .st-key-select_unidade_etapa1,
        .st-key-select_unidade_etapa1 div[data-baseweb="select"] {{
            max-width: 380px !important;
        }}

        /* A linha do telefone (DDD + Número) é um st.container() com colunas
           dentro — diferente de um campo solto, esse wrapper não encolhe
           sozinho com o align-items:center do .st-key-conteudo_publico, então
           força a largura E a centralização (margin auto) explicitamente,
           igual já é feito em .st-key-tela_login_solicitante. O rótulo
           "Telefone para contato" logo acima usa a mesma largura/centro. */
        .st-key-linha_telefone_unidade,
        .rotulo-telefone-unidade {{
            width: 100% !important;
            max-width: 380px !important;
            margin-left: auto !important;
            margin-right: auto !important;
        }}

        /* No celular, o Streamlit por padrão empilha as colunas (DDD em cima,
           Número embaixo) em telas estreitas. Aqui força ficarem lado a lado
           sempre — DDD mais estreito, Número mais largo — igual no computador. */
        .st-key-linha_telefone_unidade [data-testid="stHorizontalBlock"] {{
            width: 100% !important;
            display: flex !important;
            flex-direction: row !important;
            flex-wrap: nowrap !important;
            gap: 8px !important;
        }}

        .st-key-linha_telefone_unidade [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {{
            width: auto !important;
            min-width: 0 !important;
        }}

        .st-key-linha_telefone_unidade [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(1) {{
            flex: 1 1 0px !important;
        }}

        .st-key-linha_telefone_unidade [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(2) {{
            flex: 3 1 0px !important;
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

        /* Ícone de clipe (anexo), coluna própria no Painel de Controle: sem
           fundo/caixa, só o traço. Branco e clicável quando tem anexo;
           esmaecido (e sem clique) quando não tem. */
        .celula-anexo {{
            text-align: center;
        }}

        .link-anexo-chamado {{
            color: #FFFFFF !important;
            text-decoration: none !important;
            cursor: pointer;
            display: inline-flex;
            vertical-align: middle;
        }}

        .icone-anexo-bloqueado {{
            color: rgba(255, 255, 255, 0.25) !important;
            display: inline-flex;
            vertical-align: middle;
            cursor: not-allowed;
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
            background-color: #2b2d31 !important;
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
            background-color: #1A1A1A !important;
            color: #FFFFFF !important;
            border: none !important;
            border-radius: 0 !important;
            box-shadow: none !important;
            font-size: 11px !important;
            margin-bottom: 14px !important;
        }}

        /* Pontas da barra de títulos arredondadas (como o resto da tabela),
           só na primeira e na última coluna — as células continuam coladas
           uma na outra (gap: 0), então arredondar todo mundo deixaria "vãos"
           entre as colunas do meio. */
        .st-key-painel_admin_tabela [data-testid="stHorizontalBlock"]:has(.header-box) > [data-testid="stColumn"]:first-child .header-box,
        .st-key-painel_cadastros_tabela [data-testid="stHorizontalBlock"]:has(.header-box) > [data-testid="stColumn"]:first-child .header-box,
        .st-key-painel_usuarios_admin_tabela [data-testid="stHorizontalBlock"]:has(.header-box) > [data-testid="stColumn"]:first-child .header-box {{
            border-top-left-radius: 10px !important;
            border-bottom-left-radius: 10px !important;
        }}

        .st-key-painel_admin_tabela [data-testid="stHorizontalBlock"]:has(.header-box) > [data-testid="stColumn"]:last-child .header-box,
        .st-key-painel_cadastros_tabela [data-testid="stHorizontalBlock"]:has(.header-box) > [data-testid="stColumn"]:last-child .header-box,
        .st-key-painel_usuarios_admin_tabela [data-testid="stHorizontalBlock"]:has(.header-box) > [data-testid="stColumn"]:last-child .header-box {{
            border-top-right-radius: 10px !important;
            border-bottom-right-radius: 10px !important;
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
            background-color: #1A1A1A !important;
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
               Telefone 1.0, Empresa 1.1, Ferramenta 1.2, Severidade 1.1,
               Assunto 1.3, Descrição 1.8, Anexo 0.6, Status 1.5 — mesmos
               valores do col_widths do Python), já que a regra geral de
               "vira card empilhado" força 100%/coluna única e precisa ser
               desfeita aqui. */
            .st-key-painel_admin_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {{
                width: auto !important;
                min-width: 0 !important;
            }}
            .st-key-painel_admin_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(1)  {{ flex: 1.3 1 0px !important; }}
            .st-key-painel_admin_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(2)  {{ flex: 1.1 1 0px !important; }}
            .st-key-painel_admin_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(3)  {{ flex: 1.2 1 0px !important; }}
            .st-key-painel_admin_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(4)  {{ flex: 1.6 1 0px !important; }}
            .st-key-painel_admin_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(5)  {{ flex: 1.0 1 0px !important; }}
            .st-key-painel_admin_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(6)  {{ flex: 1.1 1 0px !important; }}
            .st-key-painel_admin_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(7)  {{ flex: 1.2 1 0px !important; }}
            .st-key-painel_admin_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(8)  {{ flex: 1.1 1 0px !important; }}
            .st-key-painel_admin_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(9)  {{ flex: 1.3 1 0px !important; }}
            .st-key-painel_admin_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(10) {{ flex: 1.8 1 0px !important; }}
            .st-key-painel_admin_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(11) {{ flex: 0.6 1 0px !important; }}
            .st-key-painel_admin_tabela [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(12) {{ flex: 1.5 1 0px !important; }}

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
            background-color: #F4F4F1 !important;
            border-radius: 10px !important;
            padding: 10px !important;
        }}

        .st-key-resultado_consulta_tabela [data-testid="stHorizontalBlock"] {{
            gap: 6px !important;
        }}

        .st-key-resultado_consulta_tabela [data-testid="stColumn"] {{
            padding: 2px !important;
        }}

        /* Pedido do usuário: cabeçalhos sem bloco de cor de fundo (mesmo tom
           de branco da tabela), com uma borda sutil (não preta) no lugar */
        .st-key-resultado_consulta_tabela .header-box {{
            background-color: #F4F4F1 !important;
            color: #000000 !important;
            border: 1px solid #D9D9D3 !important;
            border-radius: 8px !important;
            box-shadow: none !important;
            font-size: 11px !important;
            margin-bottom: 6px !important;
        }}

        /* Badge de Status: contorno sutil (mesma cor das outras bordas),
           sem preenchimento, texto preto, mesmo "quadradinho de cantos
           arredondados" (border-radius 8px) usado no resto da tabela — em
           vez do formato de pílula que tinha antes */
        .st-key-resultado_consulta_tabela .badge-status {{
            background-color: transparent !important;
            border: 1px solid #D9D9D3 !important;
            border-radius: 8px !important;
            color: #000000 !important;
            display: block !important;
            width: 100% !important;
            box-sizing: border-box !important;
            text-align: center !important;
        }}

        /* Campos editáveis (texto/textarea/seletor de severidade): mesmo
           tom de branco da tabela, borda sutil, texto preto, compactos */
        .st-key-resultado_consulta_tabela .stTextInput input,
        .st-key-resultado_consulta_tabela .stTextInput div[data-baseweb],
        .st-key-resultado_consulta_tabela div[data-testid="stTextInputRootElement"],
        .st-key-resultado_consulta_tabela .stTextArea textarea,
        .st-key-resultado_consulta_tabela .stTextArea div[data-baseweb],
        .st-key-resultado_consulta_tabela div[data-testid="stTextAreaRootElement"],
        .st-key-resultado_consulta_tabela .stSelectbox div[data-baseweb="select"],
        .st-key-resultado_consulta_tabela .stSelectbox [role="group"] {{
            background-color: #F4F4F1 !important;
            color: #000000 !important;
            border: 1px solid #D9D9D3 !important;
            box-shadow: none !important;
            outline: none !important;
            border-radius: 8px !important;
            font-size: 12px !important;
            text-align: center !important;
        }}

        /* Mantém a mesma borda sutil (sem virar preta) quando o campo de
           texto/descrição/severidade está com foco */
        .st-key-resultado_consulta_tabela .stTextInput input:focus,
        .st-key-resultado_consulta_tabela .stTextInput div[data-baseweb]:focus-within,
        .st-key-resultado_consulta_tabela div[data-testid="stTextInputRootElement"]:focus-within,
        .st-key-resultado_consulta_tabela .stTextArea textarea:focus,
        .st-key-resultado_consulta_tabela .stTextArea div[data-baseweb]:focus-within,
        .st-key-resultado_consulta_tabela div[data-testid="stTextAreaRootElement"]:focus-within,
        .st-key-resultado_consulta_tabela .stSelectbox [role="group"]:focus-within {{
            border: 1px solid #D9D9D3 !important;
            box-shadow: none !important;
            outline: none !important;
        }}

        /* Essa versão do Streamlit desenha o selectbox de Severidade com
           react-aria (input[role="combobox"] + button[aria-label="Open"]
           dentro de um [role="group"]), não o antigo data-baseweb=select —
           por isso o texto/ícone precisam ser alcançados por esses
           atributos pra ficarem pretos e centralizados de verdade */
        .st-key-resultado_consulta_tabela .stSelectbox input[role="combobox"] {{
            color: #000000 !important;
            text-align: center !important;
        }}

        /* O botão da setinha (abrir a lista) tava aparecendo como um
           quadrado preto sólido — força um tamanho pequeno, sem fundo/borda
           próprios, só o ícone fino por cima do mesmo fundo branco do campo */
        .st-key-resultado_consulta_tabela .stSelectbox button[aria-label="Open"] {{
            background-color: transparent !important;
            background: transparent !important;
            border: none !important;
            box-shadow: none !important;
            width: 16px !important;
            height: 16px !important;
            min-width: 16px !important;
            padding: 0 !important;
            flex-shrink: 0 !important;
        }}

        .st-key-resultado_consulta_tabela .stSelectbox button[aria-label="Open"] svg {{
            width: 12px !important;
            height: 12px !important;
            color: #666666 !important;
            fill: #666666 !important;
        }}

        /* Alinha o campo de Severidade (que usa um componente diferente por
           baixo) com a mesma altura/centralização vertical dos outros
           campos da linha (Ferramenta, Empresa, etc.) */
        .st-key-resultado_consulta_tabela .stSelectbox [role="group"] {{
            display: flex !important;
            align-items: center !important;
            min-height: 38px !important;
            padding: 0 8px !important;
        }}

        .st-key-resultado_consulta_tabela .stSelectbox input[role="combobox"] {{
            padding: 0 !important;
        }}

        /* Centraliza também o texto escolhido dentro do seletor de Severidade
           (o data-align acima só centraliza o <input>/textarea normais — o
           conteúdo do selectbox fica num flex interno próprio) */
        .st-key-resultado_consulta_tabela .stSelectbox div[data-baseweb="select"] > div,
        .st-key-resultado_consulta_tabela .stSelectbox [role="group"] {{
            justify-content: center !important;
        }}

        .st-key-resultado_consulta_tabela .stSelectbox div[data-baseweb] *,
        .st-key-resultado_consulta_tabela .stSelectbox [role="group"] * {{
            color: #000000 !important;
            fill: #000000 !important;
        }}

        /* Pedido do usuário: a Descrição por padrão menor (não do tamanho
           da linha inteira) — mas continua "esticável" arrastando o
           cantinho, pra quem precisar ver um texto mais longo */
        .st-key-resultado_consulta_tabela .stTextArea textarea {{
            height: 40px !important;
            min-height: 40px !important;
            resize: vertical !important;
        }}

        /* Cabeçalho vazio (coluna do botão "Salvar", sem título): sem texto
           dentro, então a borda do header-box sobrava como um quadrado vazio
           por cima do botão — remove a borda/fundo só desse cabeçalho em
           branco, mantendo os outros cabeçalhos com título normalmente */
        .st-key-resultado_consulta_tabela .header-box:empty {{
            border: none !important;
            background: transparent !important;
            box-shadow: none !important;
        }}

        .st-key-resultado_consulta_tabela .celula-protocolo,
        .st-key-resultado_consulta_tabela .celula-texto {{
            font-size: 12px !important;
            padding: 8px 4px !important;
            text-align: center !important;
            color: #000000 !important;
        }}

        /* Botão "Salvar": compacto, mesmo padrão dos botões "Excluir" das
           outras planilhas administrativas — sobrescreve o estilo padrão
           (largo, azul translúcido) usado nos outros botões da tela pública */
        .st-key-resultado_consulta_tabela .stButton {{
            display: flex !important;
            justify-content: center !important;
        }}

        .st-key-resultado_consulta_tabela .stButton > button {{
            background-color: #F4F4F1 !important;
            color: #000000 !important;
            border: 1px solid #D9D9D3 !important;
            box-shadow: none !important;
            border-radius: 8px !important;
            width: auto !important;
            min-width: 84px !important;
            max-width: none !important;
            padding: 6px 12px !important;
            min-height: auto !important;
            margin: 4px auto !important;
        }}

        .st-key-resultado_consulta_tabela .stButton > button p {{
            font-size: 12px !important;
            font-weight: 600 !important;
            white-space: nowrap !important;
            color: #000000 !important;
        }}

        .st-key-resultado_consulta_tabela .stButton > button:hover {{
            background-color: #EAEAE4 !important;
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
    # O texto "Área Administrativa" foi removido (pedido do usuário) — os
    # campos de Usuário/Senha/Entrar não ficam mais aqui na sidebar — foram
    # movidos pro canto superior direito da tela principal (pedido do
    # usuário, layout em PC). Ver bloco "LOGIN ADMIN (CANTO SUPERIOR
    # DIREITO)", logo depois do fechamento dessa sidebar.
    if st.session_state["usuario_logado"]:
        # Logo do F4 Connect HelpDesk no topo da sidebar — só aparece pro
        # administrador logado (Felipe/Rafael), pedido do usuário.
        st.markdown(
            f'<div class="logo-sidebar-admin"><img src="{logo_sidebar_admin_src}"></div>',
            unsafe_allow_html=True,
        )
        st.success(f"Logado como: **{st.session_state['usuario_logado']}**")

        # Destaca (fundo verde-escuro) a opção da sidebar correspondente à
        # tela que está aberta agora, do mesmo jeito que o efeito de hover
        _mapa_aba_para_key = {
            "chamados": "nav_chamados",
            "insights": "nav_insights",
            "empresa": "nav_empresa",
            "ferramenta": "nav_ferramenta",
            "unidade": "nav_unidade",
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

        if st.button("Chamados", key="nav_chamados"):
            st.session_state["aba_admin"] = "chamados"
            st.rerun()

        # ---- INSIGHTS (painel de gráficos/indicadores dos chamados) ----
        if st.button("Insights", key="nav_insights"):
            if st.session_state["aba_admin"] == "insights":
                st.session_state["aba_admin"] = "chamados"
            else:
                st.session_state["aba_admin"] = "insights"
            st.rerun()

        st.markdown("---")

        # ---- CADASTRAR EMPRESA ----
        if st.button("Cadastrar empresa", key="nav_empresa"):
            # clicar de novo no mesmo texto fecha o campo
            if st.session_state["aba_admin"] == "empresa":
                st.session_state["aba_admin"] = "chamados"
            else:
                st.session_state["aba_admin"] = "empresa"
            st.rerun()

        if st.session_state["aba_admin"] == "empresa":
            nova_empresa = st.text_input("Nome da empresa", key="input_nova_empresa")
            if st.button("Salvar empresa", key="salvar_empresa"):
                if nova_empresa.strip():
                    adicionar_empresa(nova_empresa.strip(), st.session_state["usuario_logado"])
                    st.success("Empresa cadastrada!")
                    st.rerun()
                else:
                    st.warning("Digite o nome da empresa.")

        # ---- CADASTRAR FERRAMENTA ----
        if st.button("Cadastrar Ferramenta", key="nav_ferramenta"):
            if st.session_state["aba_admin"] == "ferramenta":
                st.session_state["aba_admin"] = "chamados"
            else:
                st.session_state["aba_admin"] = "ferramenta"
            st.rerun()

        if st.session_state["aba_admin"] == "ferramenta":
            nova_ferramenta = st.text_input("Nome da ferramenta", key="input_nova_ferramenta")
            if st.button("Salvar ferramenta", key="salvar_ferramenta"):
                if nova_ferramenta.strip():
                    adicionar_ferramenta(nova_ferramenta.strip(), st.session_state["usuario_logado"])
                    st.success("Ferramenta cadastrada!")
                    st.rerun()
                else:
                    st.warning("Digite o nome da ferramenta.")

        # ---- CADASTRAR UNIDADE (ClickLog Transportes: matriz/filiais) ----
        if st.button("Cadastrar Unidade", key="nav_unidade"):
            if st.session_state["aba_admin"] == "unidade":
                st.session_state["aba_admin"] = "chamados"
            else:
                st.session_state["aba_admin"] = "unidade"
            st.rerun()

        if st.session_state["aba_admin"] == "unidade":
            nova_unidade = st.text_input(
                "Nome da unidade (ex: Matriz - Cachoeirinha/RS ou Filial - Caxias)",
                key="input_nova_unidade",
            )
            if st.button("Salvar unidade", key="salvar_unidade"):
                if nova_unidade.strip():
                    adicionar_unidade(nova_unidade.strip(), st.session_state["usuario_logado"])
                    st.success("Unidade cadastrada!")
                    st.rerun()
                else:
                    st.warning("Digite o nome da unidade.")

        # ---- CADASTRAR ADMINISTRADOR ----
        if st.button("Cadastrar Administrador", key="nav_admin"):
            if st.session_state["aba_admin"] == "usuarios":
                st.session_state["aba_admin"] = "chamados"
            else:
                st.session_state["aba_admin"] = "usuarios"
            st.rerun()

        if st.session_state["aba_admin"] == "usuarios":
            novo_admin_usuario = st.text_input("Nome de usuário", key="input_novo_admin_usuario")
            novo_admin_email = st.text_input("E-mail", key="input_novo_admin_email")

            if st.button("Cadastrar administrador", key="salvar_novo_admin"):
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
        if st.button("Alterar senha", key="nav_senha"):
            abrir = not st.session_state["mostrar_alterar_senha"]
            st.session_state["mostrar_alterar_senha"] = abrir
            if abrir:
                # sempre que reabrir o painel, começa do zero (pede um código novo)
                st.session_state["codigo_senha_enviado"] = False
            st.rerun()

        if st.session_state["mostrar_alterar_senha"]:
            if not st.session_state.get("codigo_senha_enviado"):
                st.caption("Vamos enviar um código para o seu e-mail cadastrado.")
                if st.button("Enviar código por e-mail", key="enviar_codigo_senha"):
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

                if st.button("Confirmar e trocar senha", key="salvar_nova_senha"):
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

                if st.button("Reenviar código", key="reenviar_codigo_senha"):
                    st.session_state["codigo_senha_enviado"] = False
                    st.rerun()

        st.markdown("---")
        if st.button("Sair (Logout)", key="nav_logout"):
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
        '<div class="titulo-painel-chamados">Painel de Controle - Central de Chamados</div>',
        unsafe_allow_html=True,
    )

    chamados = listar_chamados()
    if not chamados:
        st.info("Nenhum chamado cadastrado até o momento.")
        return

    # 1. 12 BLOCOS DE TITULOS/CABEÇALHO (Telefone e Anexo adicionados a
    # pedido do usuário — telefone de contato da unidade/filial/parceiro, e
    # um ícone pra abrir o arquivo anexado na abertura do chamado).
    col_widths = [1.3, 1.1, 1.2, 1.6, 1.0, 1.1, 1.2, 1.1, 1.3, 1.8, 0.6, 1.5]
    headers = ["Atendente", "Protocolo", "Solicitante", "E-mail", "Telefone", "Empresa", "Ferramenta", "Severidade", "Assunto", "Descrição", "Anexo", "Status"]

    with st.container(key="painel_admin_tabela"):
        cols_head = st.columns(col_widths)
        for col, h in zip(cols_head, headers):
            col.markdown(f'<div class="header-box">{h}</div>', unsafe_allow_html=True)

        # 2. Exibição das linhas com o Seletor de Atendente
        for c in chamados:
            (
                c_atend, c_proto, c_nome, c_mail, c_tel, c_emp,
                c_ferr, c_sev, c_ass, c_desc, c_anexo, c_stat,
            ) = st.columns(col_widths)

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
            c_tel.markdown(f'<div class="celula-texto"><span class="mobile-label">Telefone:</span>{c.get("telefone_contato") or "-"}</div>', unsafe_allow_html=True)
            c_emp.markdown(f'<div class="celula-texto"><span class="mobile-label">Empresa:</span>{c.get("empresa", "-")}</div>', unsafe_allow_html=True)
            c_ferr.markdown(f'<div class="celula-texto"><span class="mobile-label">Ferramenta:</span>{c.get("ferramenta", "-")}</div>', unsafe_allow_html=True)
            c_sev.markdown(f'<div class="celula-texto"><span class="mobile-label">Severidade:</span>{formatar_severidade_admin(c.get("severidade"))}</div>', unsafe_allow_html=True)
            c_ass.markdown(f'<div class="celula-texto"><span class="mobile-label">Assunto:</span>{c.get("assunto", "-")}</div>', unsafe_allow_html=True)
            c_desc.markdown(f'<div class="celula-texto"><span class="mobile-label">Descrição:</span>{c.get("descricao", "-")}</div>', unsafe_allow_html=True)

            # Ícone de clipe (anexo), numa coluna própria — se o chamado tem
            # um arquivo, o clipe aparece branco e clicável (abre a imagem/PDF
            # numa aba nova); se não tem, o mesmo clipe aparece "apagado"
            # (esmaecido), sem link, indicando que não tem nada pra abrir.
            anexo_url = c.get("anexo_url")
            if anexo_url:
                html_anexo = (
                    f'<a href="{html.escape(anexo_url)}" target="_blank" rel="noopener" '
                    f'title="Ver anexo" class="link-anexo-chamado">{ICONE_CLIPS_SVG}</a>'
                )
            else:
                html_anexo = f'<span class="icone-anexo-bloqueado" title="Nenhum anexo">{ICONE_CLIPS_SVG}</span>'
            c_anexo.markdown(f'<div class="celula-texto celula-anexo"><span class="mobile-label">Anexo:</span>{html_anexo}</div>', unsafe_allow_html=True)

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
                    )
                # rerun com escopo "fragment": atualiza só este painel,
                # sem re-executar o app inteiro (login, CSS, imagens etc.)
                st.rerun(scope="fragment")


# ------------------ VISÃO ADMIN: INSIGHTS (PAINEL TIPO BI) ------------------
# Pedido do usuário: um painel de indicadores/gráficos sobre os chamados
# (quantos aguardando/em atendimento/concluídos/cancelados/encerrados,
# ferramenta e empresa com mais chamados — total e só entre os encerrados —
# e quem mais atendeu), pra acompanhar o atendimento no dia a dia. Os
# gráficos sempre leem os chamados direto do Supabase (mesma listar_chamados()
# usada no painel de Chamados), então não existe nada pra "atualizar
# manualmente" — reflete sozinho o que estiver lá assim que a página recarrega.
def _parse_data_chamado(valor):
    """Converte o created_at (string ISO vinda do Supabase) em datetime
    com timezone, pra dar pra comparar com o filtro de período. Retorna
    None se vier vazio ou em formato inesperado (o chamado só é ignorado
    pelo filtro de período nesse caso, sem quebrar a página)."""
    if not valor:
        return None
    try:
        texto = str(valor).replace("Z", "+00:00")
        data = datetime.fromisoformat(texto)
        if data.tzinfo is None:
            data = data.replace(tzinfo=timezone.utc)
        return data
    except Exception:
        return None


def _grafico_barras_contagem(lista_chamados, campo, rotulo, valor_vazio="Não informado"):
    """Monta um gráfico de barras (top 10) contando quantos chamados cada
    valor de `campo` (ex: ferramenta, empresa, atendente) tem na lista."""
    contagem = {}
    for c in lista_chamados:
        valor = c.get(campo)
        valor = valor.strip() if isinstance(valor, str) else valor
        if not valor:
            valor = valor_vazio
        contagem[valor] = contagem.get(valor, 0) + 1

    df = pd.DataFrame(
        {rotulo: list(contagem.keys()), "Quantidade": list(contagem.values())}
    ).sort_values("Quantidade", ascending=False).head(10)

    fig = px.bar(df, x=rotulo, y="Quantidade", text="Quantidade")
    # width=0.35 trava a largura da barra numa fração do espaço da categoria
    # — sem isso, com pouca(s) categoria(s) (ex: só 1 ferramenta no período)
    # o Plotly estica a barra pra ocupar o gráfico inteiro. Assim ela fica
    # sempre fina e proporcional, tenha 1 ou 10 barras.
    fig.update_traces(marker_color="#72A703", textposition="outside", cliponaxis=False, width=0.35)
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#FFFFFF",
        xaxis_title=None,
        yaxis_title=None,
        margin=dict(t=10, b=10, l=10, r=10),
        height=260,
        bargap=0.5,
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def painel_insights():
    st.markdown(
        '<div class="titulo-painel-chamados">Insights - Central de Chamados</div>',
        unsafe_allow_html=True,
    )

    chamados = listar_chamados()
    if not chamados:
        st.info("Nenhum chamado cadastrado até o momento.")
        return

    with st.container(key="painel_insights"):
        opcoes_periodo = {
            "Últimos 7 dias": 7,
            "Últimos 30 dias": 30,
            "Últimos 90 dias": 90,
            "Tudo": None,
        }
        # Coluna estreita pro filtro de período (pedido do usuário: mais
        # discreto, não ocupando a largura toda) — o resto da linha fica
        # livre pra outros filtros que a gente for adicionando aqui do lado.
        col_periodo, _col_espaco_filtros = st.columns([1, 3])
        with col_periodo:
            periodo_escolhido = st.selectbox(
                "Período",
                list(opcoes_periodo.keys()),
                index=1,
                key="select_periodo_insights",
            )
        dias = opcoes_periodo[periodo_escolhido]

        if dias:
            limite = datetime.now(timezone.utc) - timedelta(days=dias)
            chamados_periodo = [
                c for c in chamados
                if (data_c := _parse_data_chamado(c.get("created_at"))) and data_c >= limite
            ]
        else:
            chamados_periodo = chamados

        if not chamados_periodo:
            st.info("Nenhum chamado no período selecionado.")
            return

        # --- CONTAGEM POR STATUS ---
        st.markdown('<div class="subtitulo-insights">Chamados por status</div>', unsafe_allow_html=True)

        contagem_status = {s: 0 for s in OPCOES_STATUS}
        for c in chamados_periodo:
            if c.get("status") in contagem_status:
                contagem_status[c["status"]] += 1

        cols_metricas = st.columns(len(OPCOES_STATUS) + 1)
        cols_metricas[0].metric("Total", len(chamados_periodo))
        for col, status_nome in zip(cols_metricas[1:], OPCOES_STATUS):
            col.metric(status_nome, contagem_status[status_nome])

        df_status = pd.DataFrame({
            "Status": list(contagem_status.keys()),
            "Quantidade": list(contagem_status.values()),
        })
        df_status = df_status[df_status["Quantidade"] > 0]
        if not df_status.empty:
            fig_status = px.pie(
                df_status, names="Status", values="Quantidade", hole=0.5,
                color="Status", color_discrete_map=CORES_STATUS_INSIGHTS,
            )
            # Pedido do usuário: o percentual dentro da rosca sempre em
            # branco e negrito (por padrão o Plotly escolhe preto/branco
            # sozinho dependendo da cor da fatia — "<b>" força negrito de
            # verdade, já que a fonte do Plotly não tem uma opção separada
            # pra isso) e um pouco maior.
            fig_status.update_traces(
                texttemplate="<b>%{percent}</b>",
                textfont=dict(color="#FFFFFF", size=16),
            )
            fig_status.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font_color="#FFFFFF",
                legend_title_text="",
                margin=dict(t=10, b=10, l=10, r=10),
            )
            st.plotly_chart(fig_status, use_container_width=True, config={"displayModeBar": False})

        # --- FERRAMENTA E EMPRESA COM MAIS CHAMADOS (VOLUME TOTAL NO PERÍODO) ---
        col_ferr, col_emp = st.columns(2)
        with col_ferr:
            st.markdown('<div class="subtitulo-insights">Ferramenta com mais chamados</div>', unsafe_allow_html=True)
            _grafico_barras_contagem(chamados_periodo, "ferramenta", "Ferramenta")
        with col_emp:
            st.markdown('<div class="subtitulo-insights">Empresa com mais chamados</div>', unsafe_allow_html=True)
            _grafico_barras_contagem(chamados_periodo, "empresa", "Empresa")

        # --- A MESMA COISA, SÓ ENTRE OS CHAMADOS JÁ ENCERRADOS ---
        status_encerrados = ["Concluído", "Encerrado pelo solicitante"]
        chamados_encerrados = [c for c in chamados_periodo if c.get("status") in status_encerrados]

        col_ferr_enc, col_emp_enc = st.columns(2)
        with col_ferr_enc:
            st.markdown('<div class="subtitulo-insights">Ferramenta com mais chamados encerrados</div>', unsafe_allow_html=True)
            if chamados_encerrados:
                _grafico_barras_contagem(chamados_encerrados, "ferramenta", "Ferramenta")
            else:
                st.caption("Nenhum chamado encerrado no período.")
        with col_emp_enc:
            st.markdown('<div class="subtitulo-insights">Empresa com mais chamados encerrados</div>', unsafe_allow_html=True)
            if chamados_encerrados:
                _grafico_barras_contagem(chamados_encerrados, "empresa", "Empresa")
            else:
                st.caption("Nenhum chamado encerrado no período.")

        # --- QUEM MAIS ATENDEU ---
        st.markdown('<div class="subtitulo-insights" style="text-align: center;">Chamados por atendente</div>', unsafe_allow_html=True)
        _grafico_barras_contagem(chamados_periodo, "atendente", "Atendente", valor_vazio="Não atribuído")


# ------------------ VISÃO ADMIN: LISTA DE EMPRESAS / FERRAMENTAS CADASTRADAS ------------------
@st.fragment
def painel_cadastros(tipo):
    """
    tipo: "empresa" ou "ferramenta"
    Mostra a lista de itens cadastrados com nome, usuário que cadastrou e data.
    """
    if tipo == "empresa":
        st.markdown(
            '<div class="titulo-painel-chamados">Empresas Cadastradas</div>',
            unsafe_allow_html=True,
        )
        itens = listar_empresas_detalhado()
        func_remover = remover_empresa
    elif tipo == "unidade":
        st.markdown(
            '<div class="titulo-painel-chamados">Unidades Cadastradas</div>',
            unsafe_allow_html=True,
        )
        itens = listar_unidades_detalhado()
        func_remover = remover_unidade
    else:
        st.markdown(
            '<div class="titulo-painel-chamados">Ferramentas Cadastradas</div>',
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
        '<div class="titulo-painel-chamados">Administradores Cadastrados</div>',
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
        '<div class="titulo-resultado-consulta">Resultado da Consulta:</div>',
        unsafe_allow_html=True,
    )

    col_widths = [1.1, 1.2, 1.6, 1.1, 1.2, 1.3, 1.3, 1.8, 1.3, 0.9]
    headers = ["Protocolo", "Solicitante", "E-mail", "Empresa", "Ferramenta", "Severidade", "Assunto", "Descrição", "Status", ""]
    opcoes_severidade = ["Baixa", "Média", "Alta", "Crítica"]

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

            sev_atual = normalizar_severidade(c.get("severidade")) or opcoes_severidade[0]
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

            if c_salvar.button("Salvar", key=f"salvar_edicao_{protocolo}"):
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
                '<div class="titulo-pendentes">Solicitações de acesso pendentes</div>',
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
    elif st.session_state["aba_admin"] == "unidade":
        painel_cadastros("unidade")
    elif st.session_state["aba_admin"] == "usuarios":
        painel_usuarios_admin()
    elif st.session_state["aba_admin"] == "insights":
        painel_insights()
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
                if st.button("Abrir um novo chamado", key="btn_abrir_chamado"):
                    st.session_state["opcao_menu"] = "abrir"
                    st.rerun()

                if st.button("Acompanhar meu chamado", key="btn_acompanhar_chamado"):
                    st.session_state["opcao_menu"] = "acompanhar"
                    st.rerun()

                if st.button("Avaliar um atendimento", key="btn_avaliar_atendimento"):
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
                    '<div class="fala-titulo-sem-balao titulo-identificacao">Identificação Inicial:</div>',
                    unsafe_allow_html=True,
                )

                if st.session_state["ultimo_protocolo"]:
                    st.markdown(
                        f"""
                        <div class="card-sucesso">
                            <b>Chamado registrado com sucesso!</b><br>
                            Seu Protocolo: <b style="font-size: 20px; color: #000000;">{st.session_state['ultimo_protocolo']}</b>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    if st.session_state["ultimo_email_falhou"]:
                        st.warning(
                            "Não conseguimos enviar o e-mail de confirmação. "
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

                # Campo "Selecionar Unidade" só aparece pra quem é da ClickLog
                # Transportes (empresa com matriz + filiais); pras demais
                # empresas (ou "Outra") esse campo nem existe na tela.
                unidade = None
                eh_clicklog = empresa == "ClickLog Transportes"
                if eh_clicklog:
                    unidades_cadastradas = listar_unidades()
                    unidade = st.selectbox(
                        "Selecione sua unidade",
                        ["Selecione..."] + unidades_cadastradas,
                        index=0,
                        key="select_unidade_etapa1"
                    )

                # Se a unidade escolhida for uma filial OU um parceiro (qualquer
                # nome que contenha a palavra "Filial" ou "Parceiro"), pede
                # telefone de contato — só a matriz não precisa, porque dá
                # pra ir pessoalmente.
                _unidade_normalizada = unidade.strip().lower() if unidade else ""
                precisa_telefone = bool(unidade) and unidade != "Selecione..." and (
                    "filial" in _unidade_normalizada or "parceiro" in _unidade_normalizada
                )
                telefone_ddd = ""
                telefone_numero = ""
                if precisa_telefone:
                    st.markdown(
                        '<div class="rotulo-telefone-unidade">Telefone para contato</div>',
                        unsafe_allow_html=True,
                    )
                    with st.container(key="linha_telefone_unidade"):
                        col_ddd, col_numero = st.columns([1, 3])
                        with col_ddd:
                            telefone_ddd = st.text_input(
                                "DDD",
                                max_chars=2,
                                placeholder="(51)",
                                key="input_ddd_unidade",
                            )
                        with col_numero:
                            telefone_numero = st.text_input(
                                "Número",
                                max_chars=10,
                                placeholder="99999-9999",
                                key="input_numero_unidade",
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
                            st.warning("Selecione a empresa da qual você faz parte.")
                        elif eh_clicklog and (not unidade or unidade == "Selecione..."):
                            st.warning("Selecione sua unidade.")
                        elif precisa_telefone and (not telefone_ddd.strip() or not telefone_numero.strip()):
                            st.warning("Informe o telefone para contato (DDD e número).")
                        elif len(partes_nome) < 2:
                            st.warning("Digite seu nome completo (no mínimo Nome e Sobrenome).")
                        else:
                            st.session_state["temp_empresa"] = empresa
                            st.session_state["temp_nome"] = nome_limpo
                            st.session_state["temp_unidade"] = unidade if eh_clicklog else None
                            st.session_state["temp_telefone"] = (
                                f"({telefone_ddd.strip()}) {telefone_numero.strip()}" if precisa_telefone else None
                            )
                            st.session_state["etapa_abertura"] = 2
                            st.rerun()

                    if st.button("← Voltar ao Menu", key="btn_voltar_etapa1"):
                        st.session_state["opcao_menu"] = "inicio"
                        st.rerun()

            elif st.session_state["etapa_abertura"] == 2:
                st.markdown(
                    '<div class="fala-titulo-sem-balao titulo-identificacao">Detalhes do Chamado:</div>',
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
                            "Baixa",
                            "Média",
                            "Alta",
                            "Crítica"
                        ]
                    )

                    assunto = st.text_input("Assunto do chamado")
                    descricao = st.text_area("Descrição detalhada do problema", placeholder="Conte-nos o que está acontecendo...")

                    anexo = st.file_uploader(
                        "Anexar um arquivo (opcional)",
                        type=["png", "jpg", "jpeg", "pdf"],
                        key="uploader_anexo_chamado",
                    )

                with st.container(key="etapa2_botoes"):
                    if st.button("Enviar Chamado", key="btn_enviar_chamado"):
                        if not email or "@" not in email:
                            st.warning("Digite um e-mail válido.")
                        elif ferramenta == "Selecione...":
                            st.warning("Selecione a ferramenta.")
                        elif severidade == "Selecione...":
                            st.warning("Selecione a severidade do chamado.")
                        elif not assunto.strip():
                            st.warning("Informe o assunto.")
                        elif not descricao.strip():
                            st.warning("Descreva detalhadamente o problema.")
                        else:
                            # 1. Salva no banco
                            protocolo = salvar_chamado_supabase(
                                st.session_state["temp_nome"],
                                email,
                                st.session_state["temp_empresa"],
                                ferramenta,
                                assunto,
                                descricao,
                                severidade, # <--- PASSANDO A SEVERIDADE
                                st.session_state.get("temp_unidade"),
                                st.session_state.get("temp_telefone"),
                            )
                            # 2. Sobe o anexo (se tiver um) e grava a URL no chamado —
                            # feito depois do insert porque o protocolo só existe
                            # a partir daqui, e ele é usado como nome do arquivo.
                            # Se o upload falhar, o chamado já foi salvo mesmo assim.
                            if anexo is not None:
                                anexo_url = enviar_anexo_chamado(protocolo, anexo)
                                atualizar_anexo_chamado(protocolo, anexo_url)

                            # 3. --- DISPARA O E-MAIL INICIAL ---
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
                            st.session_state["temp_unidade"] = None
                            st.session_state["temp_telefone"] = None
                            st.rerun()

                    if st.button("← Voltar Etapa", key="btn_voltar_etapa2"):
                        st.session_state["etapa_abertura"] = 1
                        st.rerun()

        elif st.session_state["opcao_menu"] == "acompanhar":
            st.markdown(
                '<div class="fala-titulo-sem-balao titulo-identificacao">Consulte seu chamado:</div>',
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
            if st.button("Pesquisar", key="btn_pesquisar_chamado"):
                termo_limpo = termo_busca.strip()
                # Guardado pra, se a busca não encontrar nada, decidir qual aviso
                # mostrar mais abaixo (formato incompleto x protocolo/e-mail
                # corretos mas sem chamado cadastrado) — precisa ficar salvo na
                # sessão porque esse trecho só roda no clique; o aviso em si é
                # exibido depois, lá na seção de resultado.
                st.session_state["ultimo_termo_busca"] = termo_limpo

                if not termo_limpo:
                    st.warning("Digite um número de protocolo ou e-mail para pesquisar.")
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
                '<div class="fala-titulo-sem-balao titulo-identificacao">Deixe sua avaliação:</div>',
                unsafe_allow_html=True,
            )
            proto_input = st.text_input(
                "Número do Protocolo Concluído",
                placeholder="Ex: F4-PXA6A4",
                key="input_protocolo_avaliar"
            )

            if st.button("Buscar Chamado", key="btn_buscar_chamado_avaliar"):
                termo_limpo = proto_input.strip()
                if not termo_limpo:
                    st.warning("Por favor, informe o número do protocolo.")
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
                            st.error("Nenhum chamado foi encontrado com esse número de protocolo.")

            # Se encontrou o chamado, exibe os detalhes e a interface de avaliação
            if "chamado_para_avaliar" in st.session_state and st.session_state["chamado_para_avaliar"]:
                chamado = st.session_state["chamado_para_avaliar"]
                status_atual = str(chamado.get("status", "")).strip().lower()

                # Permite avaliação para 'concluído' OU 'encerrado pelo solicitante'
                status_permitidos = ["concluído", "concluido", "encerrado pelo solicitante"]

                if status_atual not in status_permitidos:
                    st.warning(
                        f"Este chamado está com o status **'{chamado.get('status')}'**. "
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
                        nota_final = st.slider("Selecione de 1 a 5 Estrelas", min_value=1, max_value=5, value=5)

                    comentario = st.text_area(
                        "Opções de melhoria / Comentários (Opcional)",
                        placeholder="Conte-nos o que achou do atendimento ou o que podemos melhorar...",
                        key="textarea_comentario_avaliacao"
                    )

                    if st.button("Enviar Avaliação", key="btn_enviar_avaliacao"):
                        if supabase:
                            supabase.table("chamados").update({
                                "nota_avaliacao": nota_final,
                                "comentario_avaliacao": comentario.strip()
                            }).eq("protocolo", chamado.get("protocolo")).execute()

                            st.success("Muito obrigado! Sua avaliação foi enviada com sucesso.")
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
                            "Digite o número do protocolo completo, incluindo o símbolo "
                            "**#**, a sigla **F4** e o traço. Exemplo: **#F4-AXWR25**"
                        )
                    else:
                        st.error(
                            "Seu chamado não foi encontrado. Confirme se o protocolo "
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
                        st.warning("Preencha usuário e senha.")
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
                '<div class="fala-titulo-sem-balao titulo-identificacao">Criar conta:</div>',
                unsafe_allow_html=True,
            )

            novo_nome_completo_sol = st.text_input(
                "Nome completo",
                placeholder="ex: Felipe Camboim",
                key="input_novo_nome_completo_solicitante",
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
                if st.button("Criar usuário", key="btn_criar_usuario_solicitante"):
                    with st.spinner("Iniciando análise, aguarde..."):
                        resultado_criacao = criar_solicitacao_conta(
                            novo_nome_completo_sol, novo_usuario_sol, novo_email_sol, nova_senha_sol_criar
                        )
                    if resultado_criacao["ok"]:
                        if resultado_criacao.get("aprovado_automaticamente"):
                            st.success(
                                "Seu cadastro foi criado! Você já pode entrar normalmente."
                            )
                        else:
                            st.success(
                                "Solicitação enviada para um administrador. Assim que ele "
                                "aprovar, você receberá um aviso no seu e-mail."
                            )
                    else:
                        st.warning(f"{resultado_criacao['erro']}")

                if st.button("← Voltar", key="btn_voltar_criar_conta"):
                    st.session_state["mostrar_criar_conta"] = False
                    st.rerun()

    elif st.session_state["mostrar_esqueci_senha_solicitante"]:
        # ---- ESQUECI MINHA SENHA (via código enviado por e-mail) ----
        with st.container(key="tela_login_solicitante"):
            st.markdown(
                '<div class="fala-titulo-sem-balao titulo-identificacao">Esqueci minha senha:</div>',
                unsafe_allow_html=True,
            )

            if not st.session_state["codigo_senha_solicitante_enviado"]:
                usuario_recuperar = st.text_input(
                    "Seu nome de usuário", key="input_usuario_recuperar_senha"
                )

                with st.container(key="login_solicitante_botoes"):
                    if st.button("Enviar código por e-mail", key="btn_enviar_codigo_solicitante"):
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
                    if st.button("Confirmar e trocar senha", key="btn_confirmar_nova_senha_solicitante"):
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

                    if st.button("Reenviar código", key="btn_reenviar_codigo_solicitante"):
                        st.session_state["codigo_senha_solicitante_enviado"] = False
                        st.rerun()