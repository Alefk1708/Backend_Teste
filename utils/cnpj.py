import httpx
import re

def is_valid_cnpj(cnpj: str) -> bool:
    cnpj = ''.join(filter(str.isdigit, cnpj))

    if len(cnpj) != 14:
        return False

    if cnpj == cnpj[0] * 14:
        return False

    pesos1 = [5,4,3,2,9,8,7,6,5,4,3,2]
    pesos2 = [6] + pesos1

    def calc_digito(cnpj, pesos):
        soma = sum(int(cnpj[i]) * pesos[i] for i in range(len(pesos)))
        resto = soma % 11
        return '0' if resto < 2 else str(11 - resto)

    dig1 = calc_digito(cnpj[:12], pesos1)
    dig2 = calc_digito(cnpj[:12] + dig1, pesos2)

    return cnpj[-2:] == dig1 + dig2

async def verify_cnpj(cnpj: str) -> dict:
    # 1. Limpeza: Garante que só tenha números
    cnpj_limpo = re.sub(r'\D', '', cnpj)
    
    # Validação básica de tamanho antes de chamar a API
    if len(cnpj_limpo) != 14:
        return {"sucesso": False, "msg": "CNPJ com tamanho inválido"}

    url = f"https://brasilapi.com.br/api/cnpj/v1/{cnpj_limpo}"

    async with httpx.AsyncClient() as client:
        try:
            # 2. Timeout: Se a API demorar mais de 10s, aborta para não travar seu app
            response = await client.get(url, timeout=10.0)
            
            # 3. Tratamento de Status HTTP
            if response.status_code == 200:
                data = response.json()
                
                # Na BrasilAPI, o status fica em 'descricao_situacao_cadastral'
                situacao = data.get("descricao_situacao_cadastral")
                razao = data.get("razao_social")
                
                eh_ativo = situacao == "ATIVA"
                
                return {
                    "sucesso": True, 
                    "ativo": eh_ativo, 
                    "situacao": situacao,
                    "empresa": razao
                }
            
            elif response.status_code == 404:
                return {"sucesso": False, "msg": "CNPJ não encontrado na Receita."}
            
            elif response.status_code == 429:
                return {"sucesso": False, "msg": "Muitas requisições. Tente mais tarde."}
            
            else:
                return {"sucesso": False, "msg": f"Erro na API: {response.status_code}"}

        except httpx.RequestError as e:
            # Captura erros de conexão (sem internet, DNS, etc)
            return {"sucesso": False, "msg": f"Erro de conexão: {e}"}
            
        except Exception as e:
            # Captura erros genéricos de código
            return {"sucesso": False, "msg": f"Erro interno: {e}"}