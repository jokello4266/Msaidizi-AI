import os
import json
import requests
from fastapi import FastAPI, Request, Response, HTTPException, BackgroundTasks
from pydantic import BaseModel
from supabase import create_client, Client
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Msaidizi AI Automated Backend")

# Initialize Cloud Clients
supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
anthropic = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# Webhook Verification for Meta APIs
@app.get("/webhook/whatsapp")
@app.get("/webhook/instagram")
def verify_webhook(request: Request):
    params = request.query_params
    verify_token = os.getenv("META_VERIFY_TOKEN")
    
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == verify_token:
        return Response(content=params.get("hub.challenge"), media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification token mismatch")

# ==========================================
# 1. VENDOR INVENTORY UPLOAD (WHATSAPP)
# ==========================================
def process_whatsapp_upload(payload: dict):
    try:
        message = payload['entry'][0]['changes'][0]['value']['messages'][0]
        vendor_phone = message['from']
        
        # 1. Verify vendor exists
        vendor_data = supabase.table("vendors").select("*").eq("whatsapp_number", vendor_phone).execute()
        if not vendor_data.data:
            return # Ignore untracked phone numbers
        vendor = vendor_data.data[0]

        if "image" in message and "caption" in message:
            image_id = message['image']['id']
            caption = message['image']['caption']
            
            # 2. Download Image Stream from Meta Servers
            media_url_res = requests.get(f"https://graph.facebook.com/v19.0/{image_id}", headers={"Authorization": f"Bearer {os.getenv('META_ACCESS_TOKEN')}"}).json()
            image_stream = requests.get(media_url_res['url'], headers={"Authorization": f"Bearer {os.getenv('META_ACCESS_TOKEN')}"}).content
            
            # 3. Upload to Supabase Storage Bucket
            file_path = f"inventory/{vendor['id']}_{image_id}.jpg"
            supabase.storage.from_("product-images").upload(file_path, image_stream, {"content-type": "image/jpeg"})
            public_url = supabase.storage.from_("product-images").get_public_url(file_path)
            
            # 4. Use Claude 3.5 Sonnet Vision to parse the local seller language and caption
            vision_prompt = f"""
            Analyze this product text: "{caption}". 
            Extract data as pure JSON formatting with keys: 'item_name' (string), 'sizes' (array of integers), 'retail_price' (integer), 'floor_price' (integer).
            Note: If the caption mentions a lower price (e.g. 'Last 4000' or 'bei ya mwisho'), treat that as the 'floor_price'. If not, set 'floor_price' identical to 'retail_price'.
            """
            
            response = anthropic.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=500,
                messages=[{"role": "user", "content": vision_prompt}]
            )
            
            extracted_data = json.loads(response.content[0].text)
            
            # 5. Insert Item into PostgreSQL Inventory Database Table
            supabase.table("inventory").insert({
                "vendor_id": vendor['id'],
                "item_name": extracted_data['item_name'],
                "sizes": extracted_data['sizes'],
                "retail_price": extracted_data['retail_price'],
                "floor_price": extracted_data['floor_price'],
                "image_url": public_url
            }).execute()
            
    except Exception as e:
        print(f"Error processing WhatsApp inventory upload: {str(e)}")

@app.post("/webhook/whatsapp")
async def whatsapp_webhook_post(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    background_tasks.add_task(process_whatsapp_upload, payload)
    return {"status": "processing"}

# ==========================================
# 2. CUSTOMER SALES AGENT (INSTAGRAM DM)
# ==========================================
def process_instagram_sales(payload: dict):
    try:
        messaging_event = payload['entry'][0]['messaging'][0]
        customer_ig_id = messaging_event['sender']['id']
        customer_text = messaging_event['message']['text']
        page_id = messaging_event['recipient']['id']
        
        # 1. Identify which vendor's IG page this message belongs to
        vendor_data = supabase.table("vendors").select("*").eq("instagram_username", page_id).execute()
        if not vendor_data.data:
            return
        vendor = vendor_data.data[0]
        
        # 2. Fetch all available stock options for this vendor to give Claude direct context
        stock_data = supabase.table("inventory").select("*").eq("vendor_id", vendor['id']).eq("is_in_stock", True).execute()
        available_stock = json.dumps(stock_data.data)
        
        # 3. Handle conversation state / history tracking
        history_res = supabase.table("conversations").select("*").eq("customer_ig_id", customer_ig_id).eq("vendor_id", vendor['id']).execute()
        
        if history_res.data:
            chat_history = history_res.data[0]['chat_history']
        else:
            chat_history = []
            
        chat_history.append({"role": "user", "content": customer_text})
        
        # 4. Prompt Engineering Setup for local dialect sales behavior
        system_prompt = f"""
        You are an elite automated retail assistant running the Instagram DM sales channel for '{vendor['business_name']}' in Nairobi, Kenya.
        Your goal is to be helpful, maximize sales, handle casual bargaining, and collect delivery logistics.
        
        CRITICAL RULES:
        1. Speak in a natural, friendly blend of English, Swahili, and light urban Sheng (e.g. use greetings like Mambo, Sasa, use terms like 'form', 'wazi', 'mzigo').
        2. Here is the accurate real-time inventory available in your shop database right now: {available_stock}. 
        3. If the customer asks for a product or size you do not see in the data, politely tell them it is sold out.
        4. If the customer bargains, you can drop the price gradually to match their offer, but NEVER go below the 'floor_price' assigned to that product in the database.
        5. Once a deal is struck, instruct them to pay via M-PESA and collect their Delivery Location, Name, and Phone Number.
        """
        
        # 5. Execute conversational AI flow
        messages_payload = [{"role": m["role"], "content": m["content"]} for m in chat_history]
        ai_response = anthropic.messages.create(
            model="claude-3-5-sonnet-20241022",
            system=system_prompt,
            max_tokens=800,
            messages=messages_payload
        )
        reply_text = ai_response.content[0].text
        chat_history.append({"role": "assistant", "content": reply_text})
        
        # 6. Save back the chat memory and send text out via Meta API
        if history_res.data:
            supabase.table("conversations").update({"chat_history": chat_history}).eq("id", history_res.data[0]['id']).execute()
        else:
            supabase.table("conversations").insert({"customer_ig_id": customer_ig_id, "vendor_id": vendor['id'], "chat_history": chat_history}).execute()
            
        # Send reply out to Meta Instagram Send API
        requests.post(
            f"https://graph.facebook.com/v19.0/me/messages?access_token={os.getenv('META_ACCESS_TOKEN')}",
            json={"recipient": {"id": customer_ig_id}, "message": {"text": reply_text}}
        )
        
    except Exception as e:
        print(f"Error handling Instagram sales conversion loop: {str(e)}")

@app.post("/webhook/instagram")
async def instagram_webhook_post(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    background_tasks.add_task(process_instagram_sales, payload)
    return {"status": "processing"}
