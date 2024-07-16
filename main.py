#!/usr/bin/env python
# This program is dedicated to the public domain under the CC0 license.
# pylint: disable=import-error,unused-argument
"""
Simple example of a bot that uses a custom webhook setup and handles custom updates.
For the custom webhook setup, the libraries `flask`, `asgiref` and `uvicorn` are used. Please
install them as `pip install flask[async]~=2.3.2 uvicorn~=0.23.2 asgiref~=3.7.2`.
Note that any other `asyncio` based web server framework can be used for a custom webhook setup
just as well.

Usage:
Set bot Token, URL, admin CHAT_ID and PORT after the imports.
You may also need to change the `listen` value in the uvicorn configuration to match your setup.
Press Ctrl-C on the command line or send a signal to the process to stop the bot.
"""
import os
import mysql
import mysql.connector
import asyncio
from google.cloud.sql.connector import Connector, IPTypes
import pymysql
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.orm import sessionmaker

import sqlalchemy
import html
import logging
import json
from dataclasses import dataclass
from http import HTTPStatus

import uvicorn
from asgiref.wsgi import WsgiToAsgi
from flask import Flask, Response, abort, make_response, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackContext,
    CommandHandler,
    ContextTypes,
    ExtBot,
    TypeHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler
    )
from dotenv import load_dotenv

# Load .env
load_dotenv()

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
# set higher logging level for httpx to avoid all GET and POST requests being logged
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Define configuration constants
URL = os.environ['CLOUD_URL']
ADMIN_CHAT_ID = 566682368 #TODO change
PORT = 8080
BOT_TOKEN = os.environ['BOT_TOKEN'] # nosec B105

# Database connection
# def connect_unix_socket() -> sqlalchemy.engine.base.Engine:
#     """Initializes a Unix socket connection pool for a Cloud SQL instance of MySQL."""
#     # Note: Saving credentials in environment variables is convenient, but not
#     # secure - consider a more secure solution such as
#     # Cloud Secret Manager (https://cloud.google.com/secret-manager) to help
#     # keep secrets safe.
#     db_user = os.environ["DB_USER"]  # e.g. 'my-database-user'
#     db_pass = os.environ["DB_PASS"]  # e.g. 'my-database-password'
#     db_name = os.environ["DB_NAME"]  # e.g. 'my-database'
#     unix_socket_path = os.environ[
#         "INSTANCE_UNIX_SOCKET"
#     ]  # e.g. '/cloudsql/project:region:instance'

#     pool = sqlalchemy.create_engine(
#         # Equivalent URL:
#         # mysql+pymysql://<db_user>:<db_pass>@/<db_name>?unix_socket=<socket_path>/<cloud_sql_instance_name>
#         sqlalchemy.engine.url.URL.create(
#             drivername="mysql+pymysql",
#             username=db_user,
#             password=db_pass,
#             database=db_name,
#             query={"unix_socket": unix_socket_path},
#         ),
#         # ...
#     )
#     return pool

# initialize Connector object
connector = Connector()

# function to return the database connection
def getconn() -> pymysql.connections.Connection:
    conn: pymysql.connections.Connection = connector.connect(
        "telegram-bot-job:asia-southeast1:app-reg",
        "pymysql",
        user=os.environ['DB_USER'],
        password=os.environ['DB_PASS'],
        db=os.environ['DB_NAME']
    )
    return conn

# create connection pool
pool = sqlalchemy.create_engine(
    "mysql+pymysql://",
    creator=getconn,
)

async_pool = create_async_engine(
    "mysql+asyncmy://",
    creator=getconn,
)

AsyncSessionLocal = sessionmaker(bind=async_pool, class_=AsyncSession, expire_on_commit=False)

async def async_test_db():
    async with AsyncSessionLocal() as conn:
        user_handle = "Lizzie0111"
        # Execute the query and fetch all results
        results = await conn.execute(
            sqlalchemy.text(
                f"SELECT id, agency_name FROM agencies WHERE user_handle = '{user_handle}'"
            )
        )
        agency_profiles = results.fetchall()
    logger.info(agency_profiles)
    return agency_profiles


def test_db():
    with pool.connect() as conn:
        user_handle = "Lizzie0111"
        # Execute the query and fetch all results
        agency_profiles = conn.execute(
            sqlalchemy.text(
                f"SELECT id, agency_name FROM agencies WHERE user_handle = '{user_handle}'"
            )
        ).fetchall()
    logger.info(agency_profiles)
    return

@dataclass
class WebhookUpdate:
    """Simple dataclass to wrap a custom update type"""

    user_id: int
    payload: str


class CustomContext(CallbackContext[ExtBot, dict, dict, dict]):
    """
    Custom CallbackContext class that makes `user_data` available for updates of type
    `WebhookUpdate`.
    """

    @classmethod
    def from_update(
        cls,
        update: object,
        application: "Application",
    ) -> "CustomContext":
        if isinstance(update, WebhookUpdate):
            return cls(application=application, user_id=update.user_id)
        return super().from_update(update, application)

# Bot Commands
async def start(update: Update, context: CustomContext) -> None:
    """Display a message with instructions on how to use this bot."""
    text = (
        "Welcome to Telegram Jobs Bot! :^).\n"
        "If you need help, please use the /help command!"
    )
    agency_profs = await async_test_db()
    await update.message.reply_html(text=str(agency_profs))

async def help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    '''Displays help messages'''
    payload_url = html.escape(f"{URL}/submitpayload?user_id=<your user id>&payload=<payload>")
    await update.message.reply_html(
        f"To check if the bot is still running, call <code>{URL}/healthcheck</code>.\n\n"
        f"To post a custom update, call <code>{payload_url}</code>."
    )

# Verify Payment Command

PHOTO_REQUESTED = 1
async def verifyPayment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("LOG: verifyPayment() called")
    if update.message.photo:
        logger.info("LOG: photo received")
        # Get the largest photo size
        photo = update.message.photo[-1].file_id
        context.user_data['photo'] = photo
        await update.message.reply_text(
            "Thank you! Now, I will forward this screenshot to the admin."
        )
        return await forward_photo_to_admin(update, context)
    else:
        await update.message.reply_text(
            "Please upload a screenshot."
        )
        return PHOTO_REQUESTED
    return ConversationHandler.END

async def forward_photo_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("LOG: forward_photo_to_admin() called")
    if 'photo' in context.user_data:
        # Forward photo to admin
        photo = context.user_data['photo']
        logger.info(f"CHAT ID: {update.message.chat.id}, TYPE: {type(update.message.chat.id)}")
        # Create an inline keyboard button for acknowledgment with user chat ID in the callback data
        keyboard = [[InlineKeyboardButton("Acknowledge", callback_data=str(update.message.chat.id))]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_photo(
            chat_id=ADMIN_CHAT_ID,
            photo=photo,
            caption="Dear admin, please acknowledge this photo", #TODO add details regarding transaction
            reply_markup=reply_markup
        )

        await update.message.reply_text(
            "Photo forwarded to admin."
        )
    else:
        await update.message.reply_text(
            "No screenshot submission found."
        )
    # End the conversation
    return ConversationHandler.END
    
async def admin_acknowledge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info(f"Update: {update}")
    logger.info(f"Context USER DATA: {context.user_data}")

    query = update.callback_query
    logger.info(f'Query data: {query.data}')
    await query.answer()  # Acknowledge the callback query to remove the loading state
    # # Extract the user chat ID from the callback data
    # callback_data = json.loads(query.data)
    # user_chat_id = callback_data['user_chat_id']
    
    # Edit the caption of the photo message
    await query.edit_message_caption(caption="You have acknowledged the photo.")
    
    # Notify the user
    await context.bot.send_message(chat_id=query.data, text="Your payment has been acknowledged by an admin!.")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""
    user = update.message.from_user
    logger.info("User %s canceled the conversation.", user.first_name)
    await update.message.reply_text(
        "Bye! I hope we can talk again some day.", reply_markup=ReplyKeyboardRemove()
    )

    return ConversationHandler.END

async def createProfile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    '''Creates a profile, user can choose between Agency or Applicant profile'''
    logger.info(f'{update.message}')
    username = update.message.username
    #TODO handle linking username to new profile
    
    await update.message.reply_html()

async def webhook_update(update: WebhookUpdate, context: CustomContext) -> None: # Just to handle custom webhook updates, not a bot command
    """Handle custom updates."""
    chat_member = await context.bot.get_chat_member(chat_id=update.user_id, user_id=update.user_id)
    payloads = context.user_data.setdefault("payloads", [])
    payloads.append(update.payload)
    combined_payloads = "</code>\n• <code>".join(payloads)
    text = (
        f"The user {chat_member.user.mention_html()} has sent a new payload. "
        f"So far they have sent the following payloads: \n\n• <code>{combined_payloads}</code>"
    )
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, parse_mode=ParseMode.HTML)

# Bot classes

# Main
async def main() -> None:
    """Set up PTB application and a web application for handling the incoming requests."""
    context_types = ContextTypes(context=CustomContext)
    # Here we set updater to None because we want our custom webhook server to handle the updates
    # and hence we don't need an Updater instance
    await async_test_db()
    application = (
        Application.builder().token(BOT_TOKEN).updater(None).context_types(context_types).build()
    )

    # register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help))
    # Create the ConversationHandler with states
    payment_handler = ConversationHandler(
        entry_points=[CommandHandler('verifypayment', verifyPayment)],
        states={
            PHOTO_REQUESTED: [MessageHandler(filters.PHOTO, verifyPayment)]
        },
        
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    application.add_handler(payment_handler)

    application.add_handler(TypeHandler(type=WebhookUpdate, callback=webhook_update))
    application.add_handler(CallbackQueryHandler(admin_acknowledge))
    

    # Pass webhook settings to telegram
    await application.bot.set_webhook(url=f"{URL}/telegram", allowed_updates=Update.ALL_TYPES)

    # Set up webserver
    flask_app = Flask(__name__)

    # Flask app routes
    @flask_app.post("/telegram")  # type: ignore[misc]
    async def telegram() -> Response:
        logging.info(f"MESSAGE RECEIVED: {request.json}")
        """Handle incoming Telegram updates by putting them into the `update_queue`"""
        await application.update_queue.put(Update.de_json(data=request.json, bot=application.bot))
        return Response(status=HTTPStatus.OK)

    @flask_app.route("/submitpayload", methods=["GET", "POST"])  # type: ignore[misc]
    async def custom_updates() -> Response:
        """
        Handle incoming webhook updates by also putting them into the `update_queue` if
        the required parameters were passed correctly.
        """
        try:
            user_id = int(request.args["user_id"])
            payload = request.args["payload"]
        except KeyError:
            abort(
                HTTPStatus.BAD_REQUEST,
                "Please pass both `user_id` and `payload` as query parameters.",
            )
        except ValueError:
            abort(HTTPStatus.BAD_REQUEST, "The `user_id` must be a string!")

        await application.update_queue.put(WebhookUpdate(user_id=user_id, payload=payload))
        return Response(status=HTTPStatus.OK)

    @flask_app.get("/healthcheck")  # type: ignore[misc]
    async def health() -> Response:
        """For the health endpoint, reply with a simple plain text message."""
        response = make_response("The bot is still running fine :)", HTTPStatus.OK)
        response.mimetype = "text/plain"
        return response

    webserver = uvicorn.Server(
        config=uvicorn.Config(
            app=WsgiToAsgi(flask_app),
            port=PORT,
            use_colors=False,
            host="0.0.0.0",
        )
    )

    # Run application and webserver together
    async with application:
        await application.start()
        await webserver.serve()
        await application.stop()


if __name__ == "__main__":
    asyncio.run(main())