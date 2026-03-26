# main_app.py - Main Flask Application (WebSocket + HTTP Server)
import asyncio
import websockets
import json
import re
import os
import logging
from datetime import datetime
from threading import Thread

from flask import Flask, request, jsonify
from werkzeug.security import check_password_hash

# Import shared models from models.py
from models import (
    engine, Base, Session, User, Log, Donor, DonationLog, DailyDonationTotal,
    Config, init_database_logger, get_database_logger, log_donation,
    get_streamer_earnings, get_retention_days, set_retention_days,
    desc, func, WeeklyTopSupporter, MonthlyTopSupporter, get_monthly_donation_stats,
    get_top_supporters, get_week_dates, get_month_dates, TodaysSupporter
)

# --- Setup Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger('main_server')

# Add database handler to logger
db_handler = init_database_logger()
logger.addHandler(db_handler)

# --- Flask App Setup ---
app = Flask(__name__)
main_event_loop = asyncio.new_event_loop()

# --- Global State ---
connected_clients = {}  # token -> [websocket1, websocket2, ...]
HEARTBEAT_INTERVAL = 30  # seconds
TOP_SUPPORTERS_UPDATE_INTERVAL = 300  # 5 minutes

# --- Payment Processors Configuration ---
PAYMENT_PROCESSORS = {
    "bKash": {
        "amount_pattern": r"You have received Tk\s*([\d,]+(?:\.\d{1,2})?)",
        "sender_pattern": r"from (\d{10,11})",
        "message_extraction": lambda msg, phrase: extract_bkash_message(msg, phrase)
    },
    "Nagad": {
        "amount_pattern": r"Amount: Tk\s*([\d,]+(?:\.\d{1,2})?)",
        "sender_pattern": r"Sender: (\d{10,11})",
        "message_extraction": lambda msg, phrase: extract_nagad_message(msg, phrase)
    }
    # Add more payment processors here with their extraction patterns
    # "Rocket": { ... }
}

# --- WebSocket Server ---
async def handle_connection(websocket):
    """Handle a new websocket client connection."""
    logger.info(f"New WebSocket client connected: {id(websocket)}")

    # Send welcome message
    await websocket.send(json.dumps({
        'type': 'welcome',
        'message': 'Connected to WebSocket Server',
        'timestamp': datetime.now().isoformat()
    }))

    # Setup authentication timeout task
    auth_timeout_task = asyncio.create_task(close_unauthenticated_connection(websocket, 120))  # 120 seconds = 2 minutes
    
    # Setup heartbeat task
    heartbeat_task = asyncio.create_task(send_heartbeats(websocket))
    
    # We'll initialize these later
    top_supporters_task = None
    streamer_id = None
    
    authenticated = False
    client_token = None

    # Wait for client authentication
    try:
        async for message in websocket:
            logger.info(f"Received message: {message}")
            try:
                data = json.loads(message)
                message_type = data.get('type')

                if message_type == 'authenticate':
                    token = data.get('token')
                    username = data.get('username')

                    # Verify both token and username are provided
                    if not token or not username:
                        logger.warning(f"Authentication failed: Missing token or username")
                        await websocket.send(json.dumps({
                            'type': 'auth_ack',
                            'status': 'failed',
                            'message': 'Both token and username are required'
                        }))
                        continue

                    # Check if username exists
                    session = Session()
                    try:
                        user = session.query(User).filter_by(username=username).first()
                        if not user:
                            logger.warning(f"Authentication failed: Username {username} not found")
                            await websocket.send(json.dumps({
                                'type': 'auth_ack',
                                'status': 'failed',
                                'message': 'Invalid username'
                            }))
                            continue

                        # Check if token matches
                        if user.token == token:
                            # Add this websocket to the list of connections for this token
                            if token not in connected_clients:
                                connected_clients[token] = []
                            connected_clients[token].append(websocket)
                            
                            client_token = token
                            authenticated = True
                            # Save streamer_id for top supporters updates
                            streamer_id = user.id
                            
                            # Cancel the authentication timeout since we're now authenticated
                            auth_timeout_task.cancel()
                            
                            # Setup top supporters update task
                            top_supporters_task = asyncio.create_task(
                                periodic_top_supporters_updates(websocket, streamer_id)
                            )
                            
                            # Send initial top supporters update
                            await send_top_supporters_update(websocket, streamer_id)
                            
                            logger.info(f"Client authenticated with username: {username} and token: {token}")
                            await websocket.send(json.dumps({
                                'type': 'auth_ack',
                                'status': 'success',
                                'username': username
                            }))
                        else:
                            logger.warning(f"Authentication failed: Token mismatch for {username}")
                            await websocket.send(json.dumps({
                                'type': 'auth_ack',
                                'status': 'failed',
                                'message': 'Invalid token for username'
                            }))
                    finally:
                        session.close()
                elif message_type == 'heartbeat_ack':
                    # Client acknowledged heartbeat, nothing to do
                    pass
                elif authenticated:
                    # Handle other message types when authenticated
                    logger.info(f"Received authenticated message of type: {message_type}")
                else:
                    logger.warning(f"Unhandled message type: {message_type}")
            except json.JSONDecodeError as e:
                logger.error(f"JSON decode error: {e}")
                await websocket.send(json.dumps({
                    'type': 'error',
                    'message': 'Invalid JSON format'
                }))
    except websockets.exceptions.ConnectionClosed as e:
        logger.info(f"Connection closed: {id(websocket)} ({e})")
    finally:
        # Cancel all pending tasks
        auth_timeout_task.cancel()
        heartbeat_task.cancel()
        if top_supporters_task:
            top_supporters_task.cancel()
            
        try:
            await auth_timeout_task
            await heartbeat_task
            if top_supporters_task:
                await top_supporters_task
        except asyncio.CancelledError:
            pass

        # Cleanup disconnected client
        if client_token and client_token in connected_clients:
            if websocket in connected_clients[client_token]:
                connected_clients[client_token].remove(websocket)
                logger.info(f"Removed client connection for token: {client_token}")
                
                # If this was the last connection for this token, remove the token entry
                if not connected_clients[client_token]:
                    del connected_clients[client_token]
                    logger.info(f"Removed token from connected clients: {client_token}")

async def close_unauthenticated_connection(websocket, timeout_seconds):
    """Close connection if client doesn't authenticate within the timeout period."""
    try:
        await asyncio.sleep(timeout_seconds)
        # If this code runs, it means the timeout expired without being cancelled
        logger.warning(f"Authentication timeout ({timeout_seconds}s) expired for {id(websocket)}")
        await websocket.send(json.dumps({
            'type': 'timeout',
            'message': f'Authentication timeout ({timeout_seconds}s) expired. Closing connection.',
            'timestamp': datetime.now().isoformat()
        }))
        await websocket.close(1008, "Authentication timeout")
    except asyncio.CancelledError:
        # This exception is raised when the task is cancelled (when authentication is successful)
        logger.info(f"Authentication timeout cancelled for {id(websocket)}")

async def send_heartbeats(websocket):
    """Send periodic heartbeats to detect disconnected clients."""
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await websocket.send(json.dumps({
                'type': 'heartbeat',
                'timestamp': datetime.now().isoformat()
            }))
    except (asyncio.CancelledError, websockets.exceptions.ConnectionClosed):
        # Task cancelled or connection closed
        pass
    except Exception as e:
        logger.error(f"Error in heartbeat: {e}")

async def start_websocket_server():
    server_ip = "0.0.0.0"
    server_port = 8765
    logger.info(f"Starting WebSocket server on ws://{server_ip}:{server_port}")

    async with websockets.serve(handle_connection, server_ip, server_port):
        await asyncio.Future()  # Run forever

async def send_top_supporters_update(websocket, streamer_id):
    """Send periodic top supporters updates to websocket clients with individual fields"""
    session = Session()
    try:
        supporters_data = get_top_supporters(session, streamer_id)
        
        # Access weekly and monthly supporters lists
        weekly_supporters = supporters_data["weekly"]["supporters"]
        monthly_supporters = supporters_data["monthly"]["supporters"]
        
        # Create payload with individual fields for each supporter
        payload = {
            "type": "top_supporters_update",
            # Weekly top 3
            "wk1Name": weekly_supporters[0]["name"],
            "wk1Total": weekly_supporters[0]["amount"],
            "wk2Name": weekly_supporters[1]["name"],
            "wk2Total": weekly_supporters[1]["amount"],
            "wk3Name": weekly_supporters[2]["name"],
            "wk3Total": weekly_supporters[2]["amount"],
            # Monthly top 3
            "mn1Name": monthly_supporters[0]["name"],
            "mn1Total": monthly_supporters[0]["amount"],
            "mn2Name": monthly_supporters[1]["name"],
            "mn2Total": monthly_supporters[1]["amount"],
            "mn3Name": monthly_supporters[2]["name"],
            "mn3Total": monthly_supporters[2]["amount"],
            # Period info
            "weekPeriod": f"{supporters_data['weekly']['start_date']} to {supporters_data['weekly']['end_date']}",
            "monthPeriod": f"{supporters_data['monthly']['start_date']} to {supporters_data['monthly']['end_date']}",
            "timestamp": datetime.now().isoformat()
        }
        
        await websocket.send(json.dumps(payload))
        logger.info(f"Sent top supporters update to client")
        
        # Also send today's supporters list
        await send_todays_supporters(websocket, streamer_id)
    except Exception as e:
        logger.error(f"Error sending top supporters update: {e}")
    finally:
        session.close()

def get_todays_supporters(session, streamer_id):
    """Get list of donors who donated today - Updated to use database view"""
    supporters = session.query(TodaysSupporter).filter(
        TodaysSupporter.streamer_id == streamer_id
    ).order_by(TodaysSupporter.rank).all()
    
    # Format the results to match existing application expectations
    result = []
    for supporter in supporters:
        result.append({
            "name": supporter.donor_name,
            "amount": float(supporter.total_amount)
        })
    
    return result

async def send_todays_supporters(websocket, streamer_id):
    """Send list of today's supporters to websocket client"""
    session = Session()
    try:
        today_supporters = get_todays_supporters(session, streamer_id)
        
        payload = {
            "type": "todays_supporters",
            "supporters": today_supporters,
            "date": datetime.now().date().isoformat(),
            "timestamp": datetime.now().isoformat()
        }
        
        await websocket.send(json.dumps(payload))
        logger.info(f"Sent today's supporters list to client ({len(today_supporters)} supporters)")
    except Exception as e:
        logger.error(f"Error sending today's supporters: {e}")
    finally:
        session.close()

async def periodic_top_supporters_updates(websocket, streamer_id):
    """Send periodic top supporters updates to websocket client"""
    try:
        # Find the token associated with this websocket
        token = None
        for t, connections in connected_clients.items():
            if websocket in connections:
                token = t
                break
                
        if not token:
            logger.warning("Cannot find token for websocket in periodic updates")
            return
            
        while True:
            await asyncio.sleep(TOP_SUPPORTERS_UPDATE_INTERVAL)
            
            # Get session
            session = Session()
            try:
                # Get top supporters data
                supporters_data = get_top_supporters(session, streamer_id)
                weekly_supporters = supporters_data["weekly"]["supporters"]
                monthly_supporters = supporters_data["monthly"]["supporters"]
                
                # Create payload with individual fields
                supporters_payload = {
                    "type": "top_supporters_update",
                    # Weekly top 3
                    "wk1Name": weekly_supporters[0]["name"],
                    "wk1Total": weekly_supporters[0]["amount"],
                    "wk2Name": weekly_supporters[1]["name"],
                    "wk2Total": weekly_supporters[1]["amount"],
                    "wk3Name": weekly_supporters[2]["name"],
                    "wk3Total": weekly_supporters[2]["amount"],
                    # Monthly top 3
                    "mn1Name": monthly_supporters[0]["name"],
                    "mn1Total": monthly_supporters[0]["amount"],
                    "mn2Name": monthly_supporters[1]["name"],
                    "mn2Total": monthly_supporters[1]["amount"],
                    "mn3Name": monthly_supporters[2]["name"],
                    "mn3Total": monthly_supporters[2]["amount"],
                    # Period info
                    "weekPeriod": f"{supporters_data['weekly']['start_date']} to {supporters_data['weekly']['end_date']}",
                    "monthPeriod": f"{supporters_data['monthly']['start_date']} to {supporters_data['monthly']['end_date']}",
                    "timestamp": datetime.now().isoformat()
                }
                
                # Broadcast to all clients for this token
                await broadcast_to_user_clients(token, supporters_payload)
                
                # Also send today's supporters list
                today_supporters = get_todays_supporters(session, streamer_id)
                today_payload = {
                    "type": "todays_supporters",
                    "supporters": today_supporters,
                    "date": datetime.now().date().isoformat(),
                    "timestamp": datetime.now().isoformat()
                }
                
                await broadcast_to_user_clients(token, today_payload)
            except Exception as e:
                logger.error(f"Error in periodic top supporters update: {e}")
            finally:
                session.close()
    except (asyncio.CancelledError, websockets.exceptions.ConnectionClosed):
        # Task cancelled or connection closed
        pass
    except Exception as e:
        logger.error(f"Error in top supporters update: {e}")

# --- New function to broadcast to all clients for a token ---
async def broadcast_to_user_clients(token, payload):
    """Send message to all WebSocket clients connected with the given token."""
    if token not in connected_clients or not connected_clients[token]:
        logger.warning(f"No active WebSocket connections for token: {token}")
        return False
    
    # Create a copy of the list to avoid modification during iteration
    client_connections = connected_clients[token].copy()
    sent_count = 0
    failed_connections = []
    
    for ws in client_connections:
        try:
            await ws.send(json.dumps(payload))
            sent_count += 1
        except websockets.exceptions.ConnectionClosed:
            # Connection is closed, mark for removal
            failed_connections.append(ws)
        except Exception as e:
            logger.error(f"Failed to send message to client: {e}")
            failed_connections.append(ws)
    
    # Clean up any failed connections
    for failed_ws in failed_connections:
        if failed_ws in connected_clients[token]:
            connected_clients[token].remove(failed_ws)
            logger.info(f"Removed dead connection for token: {token}")
    
    # If all connections failed, remove the token
    if not connected_clients[token]:
        del connected_clients[token]
        logger.info(f"Removed token with no valid connections: {token}")
    
    logger.info(f"Broadcast message to {sent_count} clients for token: {token}")
    return sent_count > 0

# --- Flask HTTP Endpoints ---

@app.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    username = data.get("username")
    password = data.get("password")

    session = Session()
    try:
        user = session.query(User).filter_by(username=username).first()
        if not user or not check_password_hash(user.password_hash, password):
            return jsonify({"error": "Invalid credentials"}), 401

        # Update last_login time
        user.last_login = datetime.now()
        session.commit()

        return jsonify({
            "token": user.token,
            "username": username
        })
    finally:
        session.close()

# Improved donation endpoint that provides DB operation feedback
@app.route("/donation", methods=["POST"])
def donation():
    logger.info("[HTTP] POST /donation")
    try:
        auth_header = request.headers.get("Authorization", "")
        logger.info(f"[HTTP] Authorization Header: {auth_header}")

        if not auth_header.startswith("Bearer "):
            logger.warning("[HTTP] Invalid Authorization header.")
            return jsonify({"error": "Invalid Authorization header"}), 401

        token = auth_header.replace("Bearer ", "")

        # Validate token against database
        session = Session()
        try:
            user = session.query(User).filter_by(token=token).first()
            if not user:
                logger.warning(f"[HTTP] Unauthorized token: {token}")
                return jsonify({"error": "Unauthorized"}), 403
                
            streamer_id = user.id
            streamer_username = user.username
        finally:
            session.close()

        data = request.get_json()
        message = data.get("message", "")
        sender = data.get("phone", "")
        matched_phrase = data.get("matchedPhrase", "")
        flag = data.get("flag", "app")

        logger.info(f"[HTTP] Donation Message Received: {message}")
        logger.info(f"[HTTP] From sender: {sender}")
        logger.info(f"[HTTP] Matched phrase: {matched_phrase}")
        logger.info(f"[HTTP] Flag: {flag}")

        # Determine payment processor and extract information
        payment_processor = determine_payment_processor(sender, message)
        if not payment_processor:
            logger.warning(f"[HTTP] Unknown payment processor for message: {message}")
            return jsonify({"error": "Unrecognized payment format"}), 400

        processor_config = PAYMENT_PROCESSORS.get(payment_processor)
        amount = extract_amount(message, processor_config.get("amount_pattern"))
        sender_phone = extract_sender_phone(message, processor_config.get("sender_pattern"))
        user_message = processor_config["message_extraction"](message, matched_phrase)
        donor_name = get_donor_name_from_db(sender_phone) or "Anonymous"

        logger.info(f"[HTTP] Parsed donation: donor={donor_name}, amount={amount}, phone={sender_phone}, message={user_message}")

        # Initialize response data
        response_data = {
            "donor": donor_name,
            "amount": amount,
            "paymentMethod": payment_processor,
            "message": user_message,
            "client_count": len(connected_clients.get(token, []))
        }

        # Database logging with proper error handling
        db_success = False
        donation_id = None
        
        if flag == "app":
            session = Session()
            try:
                amount_float = float(amount)
                donation_entry = log_donation(
                    session=session,
                    streamer_id=streamer_id,
                    username=streamer_username,
                    donor_phone=sender_phone,
                    donor_name=donor_name,
                    payment_method=payment_processor,
                    amount=amount_float,
                    message=user_message
                )
                session.commit()
                db_success = True
                donation_id = donation_entry.id
                logger.info(f"[DB] Successfully logged donation: {donation_id}")
                
                # Add success info to response
                response_data.update({
                    "status": "success",
                    "database_status": "saved",
                    "donation_id": donation_id
                })
                
            except Exception as e:
                session.rollback()
                logger.error(f"[ERROR] Failed to log donation to database: {e}")
                
                # Add failure info to response
                response_data.update({
                    "status": "partial_success",
                    "database_status": "failed",
                    "database_error": str(e),
                    "warning": "Donation notification sent but not saved to database"
                })
            finally:
                session.close()
        else:
            logger.info(f"[TEST] Test donation received - not logging to database")
            response_data.update({
                "status": "test_mode",
                "database_status": "skipped",
                "note": "Test donation - not saved to database"
            })

        # WebSocket notification (continue even if DB failed)
        if token not in connected_clients or not connected_clients[token]:
            logger.warning(f"[HTTP] No active WebSocket for token: {token}")
            response_data["websocket_status"] = "no_clients"
            return jsonify(response_data), 200  # Still return 200 if DB succeeded

        # WebSocket payload
        payload = {
            "type": "donation",
            "donor": donor_name,
            "amount": amount,
            "paymentMethod": payment_processor,
            "message": user_message,
            "timestamp": datetime.now().isoformat()
        }

        # Broadcast to WebSocket clients
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            future = asyncio.run_coroutine_threadsafe(
                broadcast_to_user_clients(token, payload), 
                main_event_loop
            )
            future.result(timeout=5)
            response_data["websocket_status"] = "sent"
            logger.info(f"[WS] Successfully broadcast donation to clients")
        except Exception as e:
            logger.error(f"[ERROR] Failed to broadcast donation via WebSocket: {e}")
            response_data["websocket_status"] = "failed"
            response_data["websocket_error"] = str(e)

        # Send updated top supporters (only for real donations that were saved)
        if flag == "app" and db_success:
            try:
                session = Session()
                try:
                    # Get and send top supporters update
                    supporters_data = get_top_supporters(session, streamer_id)
                    weekly_supporters = supporters_data["weekly"]["supporters"]
                    monthly_supporters = supporters_data["monthly"]["supporters"]
                    
                    supporters_payload = {
                        "type": "top_supporters_update",
                        "wk1Name": weekly_supporters[0]["name"],
                        "wk1Total": weekly_supporters[0]["amount"],
                        "wk2Name": weekly_supporters[1]["name"],
                        "wk2Total": weekly_supporters[1]["amount"],
                        "wk3Name": weekly_supporters[2]["name"],
                        "wk3Total": weekly_supporters[2]["amount"],
                        "mn1Name": monthly_supporters[0]["name"],
                        "mn1Total": monthly_supporters[0]["amount"],
                        "mn2Name": monthly_supporters[1]["name"],
                        "mn2Total": monthly_supporters[1]["amount"],
                        "mn3Name": monthly_supporters[2]["name"],
                        "mn3Total": monthly_supporters[2]["amount"],
                        "weekPeriod": f"{supporters_data['weekly']['start_date']} to {supporters_data['weekly']['end_date']}",
                        "monthPeriod": f"{supporters_data['monthly']['start_date']} to {supporters_data['monthly']['end_date']}",
                        "timestamp": datetime.now().isoformat()
                    }
                    
                    # Send top supporters update
                    future = asyncio.run_coroutine_threadsafe(
                        broadcast_to_user_clients(token, supporters_payload), 
                        main_event_loop
                    )
                    future.result(timeout=5)
                    
                    # Send today's supporters update
                    today_supporters = get_todays_supporters(session, streamer_id)
                    today_payload = {
                        "type": "todays_supporters",
                        "supporters": today_supporters,
                        "date": datetime.now().date().isoformat(),
                        "timestamp": datetime.now().isoformat()
                    }
                    
                    future = asyncio.run_coroutine_threadsafe(
                        broadcast_to_user_clients(token, today_payload), 
                        main_event_loop
                    )
                    future.result(timeout=5)
                    
                    response_data["supporters_update_status"] = "sent"
                    
                except Exception as e:
                    logger.error(f"[ERROR] Failed to send supporters update: {e}")
                    response_data["supporters_update_status"] = "failed"
                finally:
                    session.close()
            except Exception as e:
                logger.error(f"[ERROR] Failed to update supporters: {e}")
                response_data["supporters_update_status"] = "error"

        # Return appropriate HTTP status code
        if flag == "app":
            if db_success:
                return jsonify(response_data), 200  # Complete success
            else:
                return jsonify(response_data), 207  # Multi-status (partial success)
        else:
            return jsonify(response_data), 200  # Test mode success

    except Exception as e:
        logger.error(f"[ERROR] /donation endpoint: {e}")
        return jsonify({
            "error": "Internal server error",
            "details": str(e),
            "status": "error"
        }), 500
        
@app.route("/earnings", methods=["GET"])
def get_earnings():
    try:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Invalid Authorization header"}), 401

        token = auth_header.replace("Bearer ", "")

        # Validate token against database
        session = Session()
        try:
            user = session.query(User).filter_by(token=token).first()
            if not user:
                return jsonify({"error": "Unauthorized"}), 403
                
            # Get earnings for this streamer
            earnings = get_streamer_earnings(session, user.id)
            if earnings:
                return jsonify({
                    "streamer_id": earnings[0][0],
                    "username": earnings[0][1],
                    "total_earnings": float(earnings[0][2]),
                    "donation_count": int(earnings[0][3])
                })
            return jsonify({
                "streamer_id": user.id,
                "username": user.username,
                "total_earnings": 0.00,
                "donation_count": 0
            })
        finally:
            session.close()
    except Exception as e:
        logger.error(f"[ERROR] /earnings: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/contribution-history", methods=["GET"])
def get_contribution_history():
    try:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Invalid Authorization header"}), 401

        token = auth_header.replace("Bearer ", "")

        # Validate token against database
        session = Session()
        try:
            user = session.query(User).filter_by(token=token).first()
            if not user:
                return jsonify({"error": "Unauthorized"}), 403
                
            # Get daily contribution history
            daily_contributions = session.query(
                DailyDonationTotal.donation_date.label('date'),
                DailyDonationTotal.donation_count,
                DailyDonationTotal.total_amount
            ).filter_by(streamer_id=user.id)\
             .order_by(desc(DailyDonationTotal.donation_date))\
             .limit(30)\
             .all()
            
            # Get total donation count
            total_donations = session.query(func.sum(DailyDonationTotal.donation_count))\
                .filter_by(streamer_id=user.id)\
                .scalar() or 0
            
            # Convert SQLAlchemy objects to dictionaries
            result = {
                "total_donations": total_donations,
                "daily_contributions": [
                    {
                        "date": item.date.strftime("%Y-%m-%d"),
                        "donation_count": item.donation_count,
                        "total_amount": float(item.total_amount)
                    }
                    for item in daily_contributions
                ]
            }
            
            return jsonify(result)
        finally:
            session.close()
    except Exception as e:
        logger.error(f"[ERROR] /contribution-history: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/top-supporters", methods=["GET"])
def get_top_supporters_api():
    try:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Invalid Authorization header"}), 401

        token = auth_header.replace("Bearer ", "")

        # Validate token against database
        session = Session()
        try:
            user = session.query(User).filter_by(token=token).first()
            if not user:
                return jsonify({"error": "Unauthorized"}), 403
                
            # Get top supporters for this streamer
            supporters_data = get_top_supporters(session, user.id)
            return jsonify(supporters_data)
        finally:
            session.close()
    except Exception as e:
        logger.error(f"[ERROR] /top-supporters: {e}")
        return jsonify({"error": "Internal server error"}), 500

# Monthly Donations
@app.route("/monthly-donations", methods=["GET"])
def get_monthly_donations():
    try:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Invalid Authorization header"}), 401

        token = auth_header.replace("Bearer ", "")
        
        # Get optional query parameter to include all donors (default: false)
        include_all = request.args.get('include_all', 'false').lower() == 'true'

        # Validate token against database
        session = Session()
        try:
            user = session.query(User).filter_by(token=token).first()
            if not user:
                return jsonify({"error": "Unauthorized"}), 403
                
            # Get monthly donation statistics for this streamer
            stats = get_monthly_donation_stats(session, user.id)
            
            # Filter out donors with 0 contributions in current month
            # unless include_all=true parameter is set
            if not include_all:
                stats["donor_details"] = [
                    donor for donor in stats["donor_details"] 
                    if donor["current_month_amount"] > 0
                ]
                
            return jsonify(stats)
        finally:
            session.close()
    except Exception as e:
        logger.error(f"[ERROR] /monthly-donations: {e}")
        return jsonify({"error": "Internal server error"}), 500

# Todays Supportes List
@app.route("/todays-supporters", methods=["GET"])
def get_todays_supporters_api():
    try:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Invalid Authorization header"}), 401

        token = auth_header.replace("Bearer ", "")

        # Validate token against database
        session = Session()
        try:
            user = session.query(User).filter_by(token=token).first()
            if not user:
                return jsonify({"error": "Unauthorized"}), 403
                
            # Get today's supporters for this streamer
            today_supporters = get_todays_supporters(session, user.id)
            
            return jsonify({
                "supporters": today_supporters,
                "date": datetime.now().date().isoformat(),
                "timestamp": datetime.now().isoformat()
            })
        finally:
            session.close()
    except Exception as e:
        logger.error(f"[ERROR] /todays-supporters: {e}")
        return jsonify({"error": "Internal server error"}), 500

# Connected WebSocket Clients
@app.route("/connected-clients", methods=["GET"])
def get_connected_clients():
    try:
        return jsonify({
            "clients": {
                token: len(connections)
                for token, connections in connected_clients.items()
            }
        })
    except Exception as e:
        logger.error(f"[ERROR] /connected-clients: {e}")
        return jsonify({"error": "Internal server error"}), 500

# --- Helpers ---

def determine_payment_processor(sender, message):
    """Determine which payment processor sent the message"""
    if "bKash" in sender or "You have received Tk" in message:
        return "bKash"
    elif "NAGAD" in sender or "Money Received." in message:
        return "Nagad"
    # Add more payment processors as needed
    return None

def extract_amount(message, pattern):
    try:
        match = re.search(pattern, message)
        return match.group(1).replace(",", "") if match else "0.00"
    except Exception as e:
        logger.error(f"[ERROR] extract_amount: {e}")
        return "0.00"

def extract_sender_phone(message, pattern):
    try:
        match = re.search(pattern, message)
        return match.group(1).lstrip("0") if match else "Unknown"
    except Exception as e:
        logger.error(f"[ERROR] extract_sender_phone: {e}")
        return "Unknown"

def extract_bkash_message(message, phrase):
    """Extract user message from bKash SMS, between matched phrase and '. Fee'"""
    try:
        lower_message = message.lower()
        lower_phrase = phrase.lower()

        phrase_index = lower_message.find(lower_phrase)
        if phrase_index == -1:
            return ""

        # Start right after the phrase + a space
        message_start = phrase_index + len(phrase)
        if message[message_start] == " ":
            message_start += 1

        fee_index = lower_message.find(". fee", message_start)
        if fee_index == -1:
            return message[message_start:].strip()

        return message[message_start:fee_index].strip()
    except Exception as e:
        logger.error(f"[ERROR] extract_bkash_message: {e}")
        return ""

def extract_nagad_message(message, phrase):
    """Extract user message from Nagad SMS, after the reference phrase"""
    try:
        # Find the reference line which contains the phrase
        lines = message.split('\n')
        ref_line = ""
        for line in lines:
            if line.startswith("Ref:"):
                ref_line = line
                break
        
        if not ref_line:
            logger.warning(f"[ERROR] extract_nagad_message: No Ref: line found in message")
            return ""
        
        # Convert both the reference line and phrase to lowercase for case-insensitive matching
        ref_line_lower = ref_line.lower()
        phrase_lower = phrase.lower()
        
        # Find the phrase in the reference line (case insensitive)
        phrase_index = ref_line_lower.find(phrase_lower)
        if phrase_index == -1:
            logger.warning(f"[ERROR] extract_nagad_message: Phrase '{phrase}' not found in ref line '{ref_line}'")
            return ""
        
        # Calculate the end of the phrase in the original case
        phrase_end = phrase_index + len(phrase)
        
        # Extract everything after the phrase from the original reference line
        user_message = ref_line[phrase_end:].strip()
        
        # Remove the "Ref: " prefix if we're returning the whole line
        if user_message == "":
            return ""
            
        logger.info(f"[EXTRACT] Nagad message extracted: '{user_message}'")
        return user_message
    except Exception as e:
        logger.error(f"[ERROR] extract_nagad_message: {e}")
        return ""

def get_donor_name_from_db(phone_number):
    """Get donor name from database"""
    try:
        session = Session()
        try:
            phone_number = str(phone_number).strip().lstrip("0")
            donor = session.query(Donor).filter_by(phone_number=phone_number).first()

            # Store the name in a variable while session is still active
            display_name = donor.display_name if donor else None

            if display_name:
                logger.info(f"[DB] Found donor name in database: {display_name}")
            return display_name
        finally:
            session.close()
    except Exception as e:
        logger.error(f"[ERROR] get_donor_name_from_db: {e}")
        return None

# --- Run WebSocket Server in a separate thread ---
def run_websocket_server():
    asyncio.set_event_loop(main_event_loop)
    main_event_loop.run_until_complete(start_websocket_server())

# --- Run Flask with regular WSGI server ---
def run_flask_server():
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)

# --- Main ---
if __name__ == '__main__':
    print("Starting servers...")

    # Start WebSocket server in a separate thread
    websocket_thread = Thread(target=run_websocket_server)
    websocket_thread.daemon = True
    websocket_thread.start()

    print("HTTP Server running on http://localhost:5000")
    # Run Flask server
    run_flask_server()