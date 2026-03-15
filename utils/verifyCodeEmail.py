from dotenv import load_dotenv
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
import hashlib
import secrets
import os
load_dotenv();

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_EMAIL = os.getenv("SMTP_EMAIL")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

# Emails de contas de teste — recebem sempre o código fixo "000000"
# e o envio de email é ignorado automaticamente em seed_email_code.
TEST_EMAILS = {
    "admin.teste@dentistafacil.dev",
    "clinica.teste@dentistafacil.dev",
    "paciente.teste@dentistafacil.dev",
}

def is_test_account(email: str) -> bool:
    """Retorna True se o email pertence a uma conta de teste."""
    return email.lower() in TEST_EMAILS


def code_generator(email: str = "") -> tuple[str, datetime]:
    """
    Gera código de verificação e data de expiração.
    Para contas de teste, retorna sempre "000000".
    """
    if is_test_account(email):
        expira = datetime.utcnow() + timedelta(minutes=10)
        return "000000", expira

    codigo = str(secrets.randbelow(1000000)).zfill(6)
    expira = datetime.utcnow() + timedelta(minutes=10)
    return codigo, expira

def send_email_smtp(to_email: str, subject: str, html: str):
    msg = MIMEText(html, "html")
    msg["Subject"] = subject
    msg["From"] = f"Dentista Fácil <{SMTP_EMAIL}>"
    msg["To"] = to_email

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.sendmail(SMTP_EMAIL, to_email, msg.as_string())
    except Exception as e:
        print("Erro ao enviar email SMTP:", e)
        raise

HTML_TEMPLATE = """ <!DOCTYPE html>
<html lang="pt-BR">
  <head>
    <meta charset="UTF-8" />
    <title>Código de confirmação</title>
  </head>
  <body
    style="
      margin: 0;
      padding: 0;
      background-color: #f5f7fb;
      font-family: Arial, Helvetica, sans-serif;
    "
  >
    <table width="100%" cellpadding="0" cellspacing="0">
      <tr>
        <td align="center" style="padding: 40px 16px">
          <table
            width="100%"
            cellpadding="0"
            cellspacing="0"
            style="
              max-width: 480px;
              background: #ffffff;
              border-radius: 12px;
              padding: 32px;
              box-shadow: 0 10px 30px rgba(0, 0, 0, 0.08);
            "
          >
            <!-- Título -->
            <tr>
              <td align="center" style="padding-bottom: 12px">
                <h2 style="margin: 0; color: #1f2937">
                  Dentista Fácil
                </h2>
              </td>
            </tr>

            <!-- Texto -->
            <tr>
              <td style="padding-bottom: 24px; color: #4b5563; font-size: 15px">
                <p style="margin: 0 0 12px 0">
                  Olá 👋
                </p>
                <p style="margin: 0">
                  Use o código abaixo para confirmar seu acesso.
                  Ele é válido por <strong>{{MINUTES}} minutos</strong>.
                </p>
              </td>
            </tr>

            <!-- Código -->
            <tr>
              <td align="center" style="padding-bottom: 24px">
                <div
                  style="
                    display: inline-block;
                    padding: 16px 28px;
                    font-size: 28px;
                    letter-spacing: 6px;
                    font-weight: bold;
                    color: #3b82f6;
                    background: #eef2ff;
                    border-radius: 10px;
                  "
                >
                  {{CODE}}
                </div>
              </td>
            </tr>

            <!-- Aviso -->
            <tr>
              <td style="color: #6b7280; font-size: 13px">
                <p style="margin: 0">
                  Se você não solicitou este código, ignore este email.
                </p>
              </td>
            </tr>

            <!-- Rodapé -->
            <tr>
              <td
                align="center"
                style="padding-top: 32px; font-size: 12px; color: #9ca3af"
              >
                © Dentista Fácil • Todos os direitos reservados
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html> """

def seed_email_code(email: str, code: str, minutes: int):
    # Contas de teste não recebem email — o código fixo "000000" já é conhecido.
    if is_test_account(email):
        print(f"[TEST] seed_email_code ignorado para {email} (código fixo: 000000)")
        return

    html = HTML_TEMPLATE \
        .replace("{{CODE}}", code) \
        .replace("{{MINUTES}}", str(minutes))

    send_email_smtp(
        to_email=email,
        subject="Seu código de confirmação",
        html=html
    )