
from concurrent.futures import ThreadPoolExecutor

import json
import os
import requests
import cv2
import time
import logging
import asyncio
import telegram
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from urllib.parse import quote_plus
from filelock import FileLock
from io import BytesIO

# Setup environment and logging
try:
    # Try to get the file path if running as a script
    PROJECT_ROOT = Path(__file__).parent
except NameError:
    # Fallback for Jupyter/Colab where __file__ is undefined
    PROJECT_ROOT = Path(os.getcwd())

ENV = PROJECT_ROOT / '.env'
load_dotenv(ENV, override=True)
logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, config):
        """Initialize notification services"""
        self.executor = ThreadPoolExecutor(max_workers=2)
        self.config = config
        # Handle event loop for Colab/Jupyter compatibility
        try:
            self.loop = asyncio.get_event_loop()
        except RuntimeError:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            
        self._init_services()

    def _init_services(self):
        """Initialize and validate notification providers"""
        # WhatsApp initialization
        if all([os.getenv("CALLMEBOT_API_KEY"), os.getenv("RECEIVER_WHATSAPP_NUMBER")]):
            self.whatsapp_enabled = True
            self.base_url = "https://api.callmebot.com/whatsapp.php"
            logger.info("WhatsApp service initialized")
        else:
            self.whatsapp_enabled = False
            logger.warning("WhatsApp alerts disabled: Missing credentials")

        # Telegram initialization
        if token := os.getenv("TELEGRAM_TOKEN"):
            try:
                self.telegram_bot = FlareGuardBot(
                    token, os.getenv("TELEGRAM_CHAT_ID"))
                # Async init wrapper
                if self.loop.is_running():
                    # If loop is running (Colab), we scheduling it differently or just rely on lazy init
                    # For now, we'll try to create a task if possible, or just await usage
                    pass 
                else:
                    self.loop.run_until_complete(self._init_telegram())
            except Exception as e:
                logger.error(f"Telegram setup failed: {e}")
                self.telegram_bot = None
        else:
            logger.info("Telegram alerts disabled: Missing token")


    async def _init_telegram(self):
        """Async initialization for Telegram"""
        await self.telegram_bot.initialize()
        logger.info("Telegram service initialized")

    def save_frame(self, frame) -> Path:
        """Save detection frame with timestamp"""
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        filename = self.config.DETECTED_FIRES_DIR / f'alert_{timestamp}.jpg'
        cv2.imwrite(str(filename), frame)
        return filename

    def upload_image(self, image_path: Path) -> str:
        """Upload image to Imgur CDN"""
        try:
            client_id = getattr(self.config, 'IMGUR_CLIENT_ID', os.getenv('IMGUR_CLIENT_ID'))
            if not client_id:
                logger.error("Imgur Client ID is missing")
                return None
                
            response = requests.post(
                'https://api.imgur.com/3/upload',
                headers={
                    'Authorization': f'Client-ID {client_id}'},
                files={'image': image_path.open('rb')},
                timeout=10
            )
            response.raise_for_status()
            return response.json()['data']['link']
        except Exception as e:
            logger.error(f"Image upload failed: {str(e)}")
            return None

    def send_alert(self, frame, detection: str = "Fire") -> bool:
        """Non-blocking alert dispatch"""
        image_path = self.save_frame(frame)

        # Submit to background thread
        future = self.executor.submit(
            self._send_alerts_async_wrapper,
            image_path,
            detection
        )
        return True

    def _send_alerts_async_wrapper(self, image_path, detection):
        """Wrapper to handle async execution in background thread"""
        if self.whatsapp_enabled:
            self._send_whatsapp_alert(image_path, detection)
        
        if self.telegram_bot:
            # For Telegram (async), we need to run it in a loop
            try:
                # Create a new loop for this thread if needed, or run in the main loop context
                # Since this is a thread, we should probably stick to synchronous requests or 
                # run a loop here. Ideally, just use run() logic.
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self._send_telegram_alert(image_path, detection))
                loop.close()
            except Exception as e:
                logger.error(f"Telegram thread error: {e}")

    def _send_whatsapp_alert(self, image_path, detection):
        """Handle WhatsApp notification flow"""
        image_url = self.upload_image(image_path)
        if not image_url:
            logger.error("WhatsApp alert skipped: Image upload failed")
            return False

        message = f"🚨 {detection} Detected! View at {image_url}"
        encoded_msg = quote_plus(message)
        url = f"{self.base_url}?" \
            f"phone={os.getenv('RECEIVER_WHATSAPP_NUMBER')}&" \
            f"text={encoded_msg}&" \
            f"apikey={os.getenv('CALLMEBOT_API_KEY')}"

        try:
            response = requests.get(url, timeout=15)
            if response.status_code == 200:
                logger.info("WhatsApp alert delivered")
                return True
            logger.warning(
                f"WhatsApp Alert Attempt failed: HTTP {response.status_code}")
        except Exception as e:
            logger.error(f"WhatsApp request failed: {e}")
        return False

    async def _send_telegram_alert(self, image_path, detection):
        """Handle Telegram notification"""
        try:
            await self.telegram_bot.send_alert(
                image_path=image_path,
                caption=f"🚨 {detection} Detected!"
            )
        except Exception as e:
            logger.error(f"Telegram alert failed: {str(e)}")
            return False

    def cleanup(self):
        """Proper cleanup of resources"""
        self.executor.shutdown(wait=False)

    def __del__(self):
        self.cleanup()


class FlareGuardBot:
    def __init__(self, token: str, default_chat_id: str = None):
        self.logger = logging.getLogger(__name__)
        self.token = token
        self.default_chat_id = default_chat_id
        self.bot = telegram.Bot(token=self.token)
        # Encryption removed for simplicity in copy-paste usage unless strictly needed
        # We will store chat IDs in plain text or just use the default_chat_id content 
        # to avoid dependency hell with 'cryptography' keys if user didn't set them up.
        self.chat_ids = [default_chat_id] if default_chat_id else []

    async def initialize(self):
        pass

    async def send_alert(self, image_path: Path, caption: str) -> bool:
        """Send alert to registered chats"""
        if not image_path.exists():
            self.logger.error(f"Alert image missing: {image_path}")
            return False

        with open(image_path, 'rb') as f:
            image_data = f.read()

        for chat_id in self.chat_ids:
            try:
                photo = BytesIO(image_data)
                photo.name = 'image.jpg'
                async with self.bot:
                    await self.bot.send_photo(
                        chat_id=chat_id,
                        photo=photo,
                        caption=caption,
                        parse_mode='Markdown'
                    )
                self.logger.info(f"Alert sent to Telegram chat {chat_id}")
            except Exception as e:
                self.logger.error(f"Failed to send to {chat_id}: {str(e)}")

        return True
