from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from sqlalchemy.orm import Session
from database import get_db
from models.models import Notification, EmergencyRequest, Clinic, User
import json
import asyncio
from typing import Dict, List
import uuid
from datetime import datetime

router = APIRouter(prefix="/ws", tags=["websocket"])

# Gerenciador de conexões ativas
class ConnectionManager:
    def __init__(self):
        # {user_id: {"websocket": WebSocket, "user_type": "paciente|clinica", "connected_at": datetime}}
        self.active_connections: Dict[str, dict] = {}
    
    async def connect(self, websocket: WebSocket, user_id: str, user_type: str):
        await websocket.accept()
        self.active_connections[user_id] = {
            "websocket": websocket,
            "user_type": user_type,
            "connected_at": datetime.utcnow()
        }
        print(f"✅ Usuário conectado: {user_id} ({user_type})")
    
    def disconnect(self, user_id: str):
        if user_id in self.active_connections:
            del self.active_connections[user_id]
            print(f"❌ Usuário desconectado: {user_id}")
    
    async def send_to_user(self, user_id: str, message: dict):
        """Envia mensagem para um usuário específico"""
        if user_id in self.active_connections:
            websocket = self.active_connections[user_id]["websocket"]
            try:
                await websocket.send_json(message)
                return True
            except Exception as e:
                print(f"Erro ao enviar para {user_id}: {e}")
                self.disconnect(user_id)
                return False
        return False
    
    async def broadcast_to_clinics(self, message: dict, exclude_user_id: str = None, exclude_user_ids: list = None):
        """Envia mensagem para todas as clínicas online

        Parâmetros:
        - exclude_user_id: ID único a ser excluído (compatibilidade retroativa)
        - exclude_user_ids: lista de IDs a serem excluídos
        """
        sent_count = 0
        # normaliza para lista para checagem simples
        exclude_set = set()
        if exclude_user_id:
            exclude_set.add(str(exclude_user_id))
        if exclude_user_ids:
            try:
                for uid in exclude_user_ids:
                    exclude_set.add(str(uid))
            except Exception:
                pass

        for user_id, conn in self.active_connections.items():
            if conn["user_type"] == "clinica" and user_id not in exclude_set:
                success = await self.send_to_user(user_id, message)
                if success:
                    sent_count += 1
        return sent_count
    
    async def broadcast_to_nearby_clinics(self, latitude: float, longitude: float, 
                                        radius_km: float, message: dict, 
                                        db: Session):
        """Envia mensagem apenas para clínicas próximas à localização"""
        from math import radians, sin, cos, sqrt, atan2
        
        def haversine(lat1, lon1, lat2, lon2):
            R = 6371  # Raio da Terra em km
            lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
            dlat = lat2 - lat1
            dlon = lon2 - lon1
            a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
            c = 2 * atan2(sqrt(a), sqrt(1-a))
            return R * c
        
        sent_count = 0
        
        for user_id, conn in self.active_connections.items():
            if conn["user_type"] == "clinica":
                # Buscar localização da clínica no banco
                clinic = db.query(Clinic).filter(Clinic.id == user_id).first()
                
                # ========== FILTROS IMPORTANTES ==========
                if not clinic:
                    continue  # Clínica não existe
                
                if not clinic.is_online:
                    continue  # Clínica offline não recebe
                
                if not clinic.latitude or not clinic.longitude:
                    continue  # Sem localização
                
                # Calcular distância
                distance = haversine(
                    latitude, longitude, 
                    clinic.latitude, clinic.longitude
                )
                
                # Só envia se estiver dentro do raio
                if distance <= radius_km:
                    message_with_distance = {
                        **message,
                        "distance_km": round(distance, 1)
                    }
                    success = await self.send_to_user(user_id, message_with_distance)
                    if success:
                        sent_count += 1
                else:
                    # Clínica longe demais - não recebe notificação
                    print(f"   ↳ Clínica {user_id} ignorada: {distance:.1f}km > {radius_km}km")
        
        return sent_count

    async def broadcast_to_online_clinics(self, message: dict, db: Session, radius_km: float = None, 
                                         latitude: float = None, longitude: float = None):
        """
        Envia mensagem apenas para clínicas que estão ONLINE.
        Pode filtrar por raio geográfico se fornecer lat/lng.
        """
        from math import radians, sin, cos, sqrt, atan2
        
        def haversine(lat1, lon1, lat2, lon2):
            R = 6371
            lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
            dlat = lat2 - lat1
            dlon = lon2 - lon1
            a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
            c = 2 * atan2(sqrt(a), sqrt(1-a))
            return R * c
        
        sent_count = 0
        
        for user_id, conn in self.active_connections.items():
            if conn["user_type"] == "clinica":
                # Verificar no banco se está online
                clinic = db.query(Clinic).filter(Clinic.id == user_id).first()
                
                if not clinic or not clinic.is_online:
                    continue  # Pula clínicas offline
                
                # Se tiver coordenadas, verificar distância
                if radius_km and latitude and longitude and clinic.latitude and clinic.longitude:
                    distance = haversine(latitude, longitude, clinic.latitude, clinic.longitude)
                    if distance > radius_km:
                        continue  # Fora do raio
                
                success = await self.send_to_user(user_id, message)
                if success:
                    sent_count += 1
        
        return sent_count

manager = ConnectionManager()

@router.websocket("/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    """
    Endpoint WebSocket para conexão em tempo real
    
    Query params esperados:
    - user_type: "paciente" ou "clinica"
    """
    # Pegar query params
    query_params = dict(websocket.query_params)
    user_type = query_params.get("user_type", "paciente")
    
    await manager.connect(websocket, user_id, user_type)
    
    try:
        while True:
            # Aguardar mensagens do cliente (heartbeat/ping)
            data = await websocket.receive_text()
            
            try:
                message = json.loads(data)
                
                # Responder a pings para manter conexão viva
                if message.get("type") == "ping":
                    await websocket.send_json({"type": "pong", "timestamp": datetime.utcnow().isoformat()})
                
                # Marcar notificação como lida
                elif message.get("type") == "mark_read":
                    notification_id = message.get("notification_id")
                    # TODO: Atualizar no banco
                
                # Confirmação de recebimento
                elif message.get("type") == "ack":
                    print(f"ACK recebido de {user_id}: {message.get('message_id')}")
                    
            except json.JSONDecodeError:
                pass  # Ignorar mensagens não-JSON
                
    except WebSocketDisconnect:
        manager.disconnect(user_id)
    except Exception as e:
        print(f"Erro no WebSocket {user_id}: {e}")
        manager.disconnect(user_id)

# Funções auxiliares para enviar notificações específicas

async def notify_new_emergency(emergency_request: EmergencyRequest, db: Session):
    """Notifica APENAS clínicas online PRÓXIMAS sobre nova solicitação de urgência"""
    
    message = {
        "type": "new_emergency",
        "title": "🚨 Nova Urgência!",
        "body": "Paciente solicitou atendimento de urgência próximo à sua clínica",
        "data": {
            "emergency_id": str(emergency_request.id),
            "patient_id": str(emergency_request.patient_id),
            "latitude": emergency_request.latitude,
            "longitude": emergency_request.longitude,
            "description": emergency_request.description,
            "procedure_type": emergency_request.procedure_type,
            "created_at": emergency_request.created_at.isoformat()
        },
        "timestamp": datetime.utcnow().isoformat()
    }
    
    # Raio de notificação (mesmo valor usado na validação)
    NOTIFICATION_RADIUS_KM = 10.0
    
    # Enviar apenas para clínicas ONLINE e DENTRO DO RAIO
    count = await manager.broadcast_to_nearby_clinics(
        latitude=emergency_request.latitude,
        longitude=emergency_request.longitude,
        radius_km=NOTIFICATION_RADIUS_KM,
        message=message,
        db=db
    )
    
    print(f"📢 Notificação de urgência enviada para {count} clínicas em {NOTIFICATION_RADIUS_KM}km")
    return count

async def notify_emergency_accepted(patient_id: str, clinic: Clinic, appointment_id: str, payment_data: dict = None):
    """Notifica paciente que a clínica aceitou o atendimento"""
    message = {
        "type": "emergency_accepted",
        "title": "✅ Clínica encontrada!",
        "body": f"{clinic.name} aceitou seu atendimento de urgência",
        "data": {
            "appointment_id": str(appointment_id),
            "clinic_id": str(clinic.id),
            "clinic_name": clinic.name,
            "clinic_phone": clinic.phone,
            "clinic_address": clinic.address,
            "clinic_latitude": clinic.latitude,
            "clinic_longitude": clinic.longitude,
            **(payment_data or {})  
        },
        "timestamp": datetime.utcnow().isoformat()
    }
    
    success = await manager.send_to_user(str(patient_id), message)
    print(f"✅ Paciente {patient_id} notificado: {'sucesso' if success else 'falha (offline)'}")
    return success

async def notify_payment_confirmed(user_id: str, appointment_id: str, amount: float):
    """Notifica confirmação de pagamento"""
    message = {
        "type": "payment_confirmed",
        "title": "💳 Pagamento confirmado!",
        "body": f"Seu pagamento de R${amount:.2f} foi confirmado",
        "data": {
            "appointment_id": str(appointment_id),
            "amount": amount
        },
        "timestamp": datetime.utcnow().isoformat()
    }
    return await manager.send_to_user(str(user_id), message)

async def notify_appointment_reminder(user_id: str, appointment_id: str, 
                                     clinic_name: str, scheduled_at: datetime):
    """Lembrete de consulta agendada"""
    message = {
        "type": "appointment_reminder",
        "title": "⏰ Lembrete de consulta",
        "body": f"Você tem consulta em {clinic_name} às {scheduled_at.strftime('%H:%M')}",
        "data": {
            "appointment_id": str(appointment_id),
            "scheduled_at": scheduled_at.isoformat()
        },
        "timestamp": datetime.utcnow().isoformat()
    }
    return await manager.send_to_user(str(user_id), message)

async def notify_clinic_status_change(clinic_id: str, is_online: bool):
    """Notifica a própria clínica sobre mudança de status"""
    message = {
        "type": "clinic_status_changed",
        "is_online": is_online,
        "timestamp": datetime.utcnow().isoformat()
    }
    return await manager.send_to_user(str(clinic_id), message)