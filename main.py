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
import traceback
import pymysql
from typing import Callable
from google.cloud.sql.connector import Connector, IPTypes
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

#TODO change/sanitize f-string SQL entries to protect from injection attacks

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
async def get_db_fetchone(query_string: str):
    """Retrieves entry from DB

    Args:
        query_string (str): Query for DB to execute
        fetch_fn (Callable): fetchall() or fetchone(), etc

    Returns:
        Data from query
    """    
    try: 
        logger.info(f"Executing fetch query: {query_string}")
        async with AsyncSessionLocal() as conn:
            results = await conn.execute(sqlalchemy.text(query_string))
            data = results.fetchone()
            logging.info(f"Results from query: {data}")
            return data
    except Exception as e:
        logger.info(f"Error in interacting with database: {e}")

async def get_db(query_string: str):
    """Retrieves entry from DB

    Args:
        query_string (str): Query for DB to execute
        fetch_fn (Callable): fetchall() or fetchone(), etc

    Returns:
        Data from query
    """    
    try: 
        logger.info(f"Executing fetch query: {query_string}")
        async with AsyncSessionLocal() as conn:
            results = await conn.execute(sqlalchemy.text(query_string))
            data = results.fetchall()
            logging.info(f"Results from query: {data}")
            return data
    except Exception as e:
        logger.info(f"Error in interacting with database: {e}")

async def set_db(query_string: str):
    try:
        logger.info(f"Executing commit query: {query_string}")
        async with AsyncSessionLocal() as conn:
            await conn.execute(sqlalchemy.text(query_string))
            await conn.commit()
    except Exception as e:
        logger.info(f"Error in interacting with database: {e}")
    

async def async_test_db():
    user_handle = "brandonlmk"
    query_string = f"SELECT id, agency_name FROM agencies WHERE user_handle = '{user_handle}'"
    agency_profiles = await get_db(query_string)
    logger.info(agency_profiles) #
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
###########################################################################################################################################################   
# Bot Commands
# Start command
async def start(update: Update, context: CustomContext) -> None:
    """Display a message with instructions on how to use this bot."""
    text = (
        "Welcome to Telegram Jobs Bot! :^).\n"
        "If you need help, please use the /help command!"
    )
    agency_profs = await async_test_db() #TODO remove later
    await update.message.reply_html(text=str(agency_profs)) #TODO change text

# Help command
async def help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    '''Displays help messages'''
    payload_url = html.escape(f"{URL}/submitpayload?user_id=<your user id>&payload=<payload>")
    await update.message.reply_html(
        f"To check if the bot is still running, call <code>{URL}/healthcheck</code>.\n\n"
        f"To post a custom update, call <code>{payload_url}</code>."
    )

# Register command
#TODO add error/wrong input filtering/handling

NAME, DOB, PAST_EXPERIENCES, CITIZENSHIP, RACE, GENDER, HIGHEST_EDUCATION, WHATSAPP_NUMBER, FULL_NAME, COMPANY_NAME, COMPANY_UEN = range(11)

async def register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [
        [
            InlineKeyboardButton("Applicant", callback_data='applicant'),
            InlineKeyboardButton("Agency", callback_data='agency'),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('Please choose your account type:', reply_markup=reply_markup)
    return NAME

# Handle button clicks
async def register_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("clicked on register button")
    query = update.callback_query
    await query.answer()

    user_handle = update.effective_user.username
    chat_id = update.effective_chat.id

    context.user_data['user_handle'] = user_handle
    context.user_data['account_type'] = query.data
    context.user_data['chat_id'] = chat_id

    if query.data == 'applicant':
        await query.edit_message_text(text="You chose Applicant. Please enter your full name:")
        context.user_data['registration_step'] = 'name'
        return NAME
    elif query.data == 'agency':
        await query.edit_message_text(text="You chose Agency. Please enter your full name:")
        context.user_data['registration_step'] = 'full_name'
        return FULL_NAME

# Define the functions for each step
async def ask_for_dob(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Entered ask_for_dob")
    context.user_data['name'] = update.message.text
    await update.message.reply_text('Please enter your date of birth (YYYY-MM-DD):')
    context.user_data['registration_step'] = 'dob'
    return DOB

async def ask_for_past_experiences(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Entered ask_for_past_experiences")
    context.user_data['dob'] = update.message.text
    await update.message.reply_text('Please enter your past experiences to improve chances of getting shortlisted:')
    context.user_data['registration_step'] = 'past_experiences'
    return PAST_EXPERIENCES

async def ask_for_citizenship(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Entered ask_for_citizenship")
    context.user_data['past_experiences'] = update.message.text
    await update.message.reply_text('Please enter your citizenship status:')
    context.user_data['registration_step'] = 'citizenship'
    return CITIZENSHIP

async def ask_for_race(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Entered ask_for_race")
    context.user_data['citizenship'] = update.message.text
    await update.message.reply_text('Please enter your race:')
    context.user_data['registration_step'] = 'race'
    return RACE

async def ask_for_gender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Entered ask_for_gender")
    context.user_data['race'] = update.message.text
    await update.message.reply_text('Please enter your gender:')
    context.user_data['registration_step'] = 'gender'
    return GENDER

async def ask_for_highest_education(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Entered ask_for_highest_education")
    context.user_data['gender'] = update.message.text
    await update.message.reply_text('Please enter your highest education:')
    context.user_data['registration_step'] = 'highest_education'
    return HIGHEST_EDUCATION

async def ask_for_whatsapp_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Entered ask_for_whatsapp_number")
    context.user_data['highest_education'] = update.message.text
    await update.message.reply_text('Please enter your WhatsApp number:')
    context.user_data['registration_step'] = 'whatsapp_number'
    return WHATSAPP_NUMBER

async def save_applicant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Entered save_applicant")
    context.user_data['whatsapp_number'] = update.message.text
    async with AsyncSessionLocal() as conn:
        await conn.execute(
            sqlalchemy.text(
        f"INSERT INTO applicants (user_handle, name, dob, past_exp, citizenship, race, gender, education, whatsapp_no) VALUES ('{context.user_data['user_handle']}', '{context.user_data['name']}', '{context.user_data['dob']}', '{context.user_data['past_experiences']}', '{context.user_data['citizenship']}', '{context.user_data['race']}', '{context.user_data['gender']}', '{context.user_data['highest_education']}', '{context.user_data['whatsapp_number']}')"
    )
        )
        await conn.commit()
    await update.message.reply_text('Registration successful!')
    return ConversationHandler.END

async def ask_for_company_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Entered ask_for_company_name")
    context.user_data['full_name'] = update.message.text
    await update.message.reply_text('Please enter your company name:')
    context.user_data['registration_step'] = 'company_name'
    return COMPANY_NAME

async def ask_for_company_uen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Entered ask_for_company_uen")
    context.user_data['company_name'] = update.message.text
    await update.message.reply_text('Please enter your company UEN:')
    context.user_data['registration_step'] = 'company_uen'
    return COMPANY_UEN

async def save_agency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Entered save_agency")
    context.user_data['company_uen'] = update.message.text
    async with AsyncSessionLocal() as conn:
        await conn.execute(
            sqlalchemy.text(
        f"INSERT INTO agencies (user_handle, chat_id, name, agency_name, agency_uen) VALUES ('{context.user_data['user_handle']}', '{context.user_data['chat_id']}', '{context.user_data['full_name']}', '{context.user_data['company_name']}', '{context.user_data['company_uen']}')"
    )
        )
        await conn.commit()
    await update.message.reply_text('Registration successful!')
    return ConversationHandler.END

# Main text handler
async def registration_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("entered registration_text_handler")
    if 'registration_step' in context.user_data:
        step = context.user_data['registration_step']
        
        if 'previous_steps' not in context.user_data:
            logger.info("entered the first if statement")
            context.user_data['previous_steps'] = []
        context.user_data['previous_steps'].append(step)
        logger.info("STEP:", step)
        if step == 'name':
            return await ask_for_dob(update, context)
        elif step == 'dob':
            return await ask_for_past_experiences(update, context)
        elif step == 'past_experiences':
            return await ask_for_citizenship(update, context)
        elif step == 'citizenship':
            return await ask_for_race(update, context)
        elif step == 'race':
            return await ask_for_gender(update, context)
        elif step == 'gender':
            return await ask_for_highest_education(update, context)
        elif step == 'highest_education':
            return await ask_for_whatsapp_number(update, context)
        elif step == 'whatsapp_number':
            return await save_applicant(update, context)
        elif step == 'full_name':
            return await ask_for_company_name(update, context)
        elif step == 'company_name':
            return await ask_for_company_uen(update, context)
        elif step == 'company_uen':
            return await save_agency(update, context)
        
    logger.info("exited registration_text_handler")
    return ConversationHandler.END
    

###########################################################################################################################################################   
# Edit Profile Command

SELECT_PROFILE, SELECT_ATTRIBUTE, ENTER_NEW_VALUE = range(3)

# Command handler to start editing profile
async def edit_profile(update: Update, context: CallbackContext) -> int:
    user_handle = update.effective_user.username

    # Retrieve agency and applicant profiles for the user_handle
    async with AsyncSessionLocal() as conn:
        results = await conn.execute(
            sqlalchemy.text(
                f"SELECT id, agency_name FROM agencies WHERE user_handle = '{user_handle}'"
            )
        )
        agency_profiles = results.fetchall()

    async with AsyncSessionLocal() as conn:
        results = await conn.execute(
            sqlalchemy.text(
                f"SELECT id, name FROM applicants WHERE user_handle = '{user_handle}'"
            )
        )
        applicant_profiles = results.fetchall()

    # Format profiles as inline buttons
    keyboard = []
    for id, agency_name in agency_profiles:
        keyboard.append([InlineKeyboardButton(f"Agency - {agency_name}", callback_data=f"edit_profile|agency|{id}")])

    for id, applicant_name in applicant_profiles:
        keyboard.append([InlineKeyboardButton(f"Applicant - {applicant_name}", callback_data=f"edit_profile|applicant|{id}")])

    # Check if both profiles are empty
    if not agency_profiles and not applicant_profiles:
        await update.message.reply_text('You have no profiles to edit.')
        return ConversationHandler.END

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('Select the profile you want to edit:', reply_markup=reply_markup)

    return SELECT_PROFILE

# Callback function to handle profile selection
async def select_profile(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    try:
        # Extracting profile type and id from callback_data
        parts = query.data.split('|')
        profile_type = parts[1]
        profile_id = parts[2]

        context.user_data['edit_profile_type'] = profile_type
        context.user_data['edit_profile_id'] = profile_id

        if profile_type == 'agency':
            keyboard = [
                [InlineKeyboardButton("Name", callback_data='edit_attribute|agency|name')],
                [InlineKeyboardButton("Agency Name", callback_data='edit_attribute|agency|agency_name')],
                [InlineKeyboardButton("Company UEN", callback_data='edit_attribute|agency|company_uen')]
            ]
        elif profile_type == 'applicant':
            keyboard = [
                [InlineKeyboardButton("Name", callback_data='edit_attribute|applicant|name')],
                [InlineKeyboardButton("Date of Birth", callback_data='edit_attribute|applicant|dob')],
                [InlineKeyboardButton("Past Experiences", callback_data='edit_attribute|applicant|past_exp')],
                [InlineKeyboardButton("Citizenship", callback_data='edit_attribute|applicant|citizenship')],
                [InlineKeyboardButton("Race", callback_data='edit_attribute|applicant|race')],
                [InlineKeyboardButton("Gender", callback_data='edit_attribute|applicant|gender')],
                [InlineKeyboardButton("Highest Education", callback_data='edit_attribute|applicant|education')],
                [InlineKeyboardButton("WhatsApp Number", callback_data='edit_attribute|applicant|whatsapp')]
            ]

        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text('Select the attribute you want to edit:', reply_markup=reply_markup)

        return SELECT_ATTRIBUTE

    except IndexError:
        logger.info(f"Error: Malformed callback_data - {query.data}")

    return ConversationHandler.END

# Callback function to handle attribute selection
async def select_attribute(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    try:
        parts = query.data.split('|')
        profile_type = parts[1]
        attribute = parts[2]

        context.user_data['edit_attribute'] = attribute

        await query.edit_message_text(f"Please enter the new value for {attribute.replace('_', ' ').title()}:")
        
        return ENTER_NEW_VALUE

    except IndexError:
        logger.info(f"Error: Malformed callback_data - {query.data}")

    return ConversationHandler.END

# Callback function to handle new value input
async def enter_new_value(update: Update, context: CallbackContext) -> int:
    new_value = update.message.text
    profile_type = context.user_data['edit_profile_type']
    profile_id = context.user_data['edit_profile_id']
    attribute = context.user_data['edit_attribute']

    try:
        if profile_type == 'agency':
            async with AsyncSessionLocal() as conn:
                await conn.execute(
                    sqlalchemy.text(
                f"UPDATE agencies SET {attribute} = '{new_value}' WHERE id = '{profile_id}'"
                )
                )
                await conn.commit()

        elif profile_type == 'applicant':
            async with AsyncSessionLocal() as conn:
                await conn.execute(
                    sqlalchemy.text(
                f"UPDATE applicants SET {attribute} = '{new_value}' WHERE id = '{profile_id}'"
                )
                )
                await conn.commit()
        await update.message.reply_text('Profile updated successfully!')
        context.user_data.clear()  # Clear user data after successful update

    except Exception as e:
        logger.info(f"Unexpected error: {str(e)}")
        await update.message.reply_text('An error occurred while updating the profile.')

    return ConversationHandler.END

###########################################################################################################################################################   
# #Job Posting
#TODO implement forwarding job post to admin and get acknowledgement

SELECT_AGENCY, ENTER_JOB_DETAILS = range(2)

# Function to start job posting
async def job_post(update: Update, context: CallbackContext) -> int:
    user_handle = update.effective_user.username

    # Retrieve agency profiles for the user_handle
    async with AsyncSessionLocal() as conn:
        # Execute the query and fetch all results
        results = await conn.execute(
            sqlalchemy.text(
               f"SELECT id, name, agency_name FROM agencies WHERE user_handle = '{user_handle}'"
               )
        )
        agency_profiles = results.fetchall()

    if not agency_profiles:
        await update.message.reply_text('You have no agency profiles to post a job from.')
        return ConversationHandler.END

    # Format profiles as inline buttons
    keyboard = [
        [InlineKeyboardButton(f"{agency[1]} - {agency[2]}", callback_data=f"jobpost|{agency[0]}")]
        for agency in agency_profiles
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('Select the profile you want to use for job posting:', reply_markup=reply_markup)

    return SELECT_AGENCY

# Callback function to handle profile selection for job posting
async def jobpost_button(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    context.user_data['agency_id'] = query.data.split('|')[1]
    context.user_data['jobpost_step'] = 'job_title'

    await query.edit_message_text('Please enter the Job Title:')

    return ENTER_JOB_DETAILS

# Callback function to handle job posting details input
async def jobpost_text_handler(update: Update, context: CallbackContext) -> int:
    text = update.message.text

    if 'jobpost_step' in context.user_data:
        step = context.user_data['jobpost_step']

        if step == 'job_title':
            context.user_data['jobpost_job_title'] = text
            await update.message.reply_text('Please specify the Company or Industry this job belongs to:')
            context.user_data['jobpost_step'] = 'company_industry'

        elif step == 'company_industry':
            context.user_data['jobpost_company_industry'] = text
            await update.message.reply_text('Please provide the Date and Time for this job opportunity:')
            context.user_data['jobpost_step'] = 'date_time'

        elif step == 'date_time':
            context.user_data['jobpost_date_time'] = text
            await update.message.reply_text('Please state the Pay Rate for this job:')
            context.user_data['jobpost_step'] = 'pay_rate'

        elif step == 'pay_rate':
            context.user_data['jobpost_pay_rate'] = text
            await update.message.reply_text('Please describe the Job Scope and responsibilities:')
            context.user_data['jobpost_step'] = 'job_scope'

        elif step == 'job_scope':
            context.user_data['jobpost_job_scope'] = text
            await save_jobpost(context.user_data)
            await update.message.reply_text('Please note the following:\n\n'
                                            '1. No MLM jobs\n'
                                            '2. No SingPass required jobs\n'
                                            '3. If scam jobs are found, the job post will be deleted, and credits will be revoked without a refund.\n\n'
                                            'Your job posting has been forwarded to the admin. You will be informed when it has been approved.')

            return ConversationHandler.END

    return ENTER_JOB_DETAILS

async def save_jobpost(user_data):
    async with AsyncSessionLocal() as conn:
        await conn.execute(
            sqlalchemy.text(
        f"INSERT INTO job_posts (agency_id, job_title, company_industry, date_time, pay_rate, job_scope, shortlist) VALUES ('{user_data['agency_id']}', '{user_data['jobpost_job_title']}', '{user_data['jobpost_company_industry']}', '{user_data['jobpost_date_time']}', '{user_data['jobpost_pay_rate']}', '{user_data['jobpost_job_scope']}', 0)"
    )
        )
        await conn.commit()
###########################################################################################################################################################   
# Top Up Tokens Command



###########################################################################################################################################################   
# Package purchase process
async def create_transaction_entry(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id = None, package_id = None):
    """
    Creates a new entry with [status = pending] in the database transactions table with the Chat ID and Package ID provided.
    This is done after the agency has sent their payment screenshot.

    Args:
        chat_id (str): Chat ID of agency which purchased the package
        package_id (str): ID of package purchases by agency

    Return:
        transaction_id (int): ID of newly created transaction
    """    
    # uuid, package_id = str(agency_id), str(package_id)
    # agency_id, package_id = 'd845af72-439b-11ef-8a7f-42010a400002', '12'
    chat_id = str(ADMIN_CHAT_ID)
    package_id = '12'
    # Create entry in transaction table of DB
    logger.info(f"LOG: Creating a row in transaction DB table with Chat ID: {chat_id}, Package ID: {package_id}")
    query_string = f"INSERT INTO transactions (chat_id, package_id) VALUES ('{chat_id}', '{package_id}')"
    await set_db(query_string)
    # Get transaction ID of the newly created entry
    query_string = f"SELECT transaction_id FROM transactions WHERE chat_id = '{chat_id}' ORDER BY transaction_id DESC LIMIT 1"
    results = await get_db_fetchone(query_string)
    transaction_id = results[0]
    logger.info(f"LOG: Transaction created - ID: {transaction_id}")
    context.user_data['transaction_id'] = transaction_id
    await update.message.reply_text("Transaction created!")
    return transaction_id

PHOTO_REQUESTED = 1
async def verifyPayment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Requests for screenshot of payment from Agency and calls the admin acknowledgement function

    Args:
        package_id: Package ID which agency wants to purchase
    """    
    chat_id = update.effective_chat.id
    package_id = '12'
    logger.info("LOG: verifyPayment() called")
    if update.message.photo:
        logger.info("LOG: photo received")
        # Get the largest photo size
        photo = update.message.photo[-1].file_id
        context.user_data['photo'] = photo
        transaction_id = await create_transaction_entry(update, context, chat_id=chat_id, package_id=package_id)
        await update.message.reply_text(
            "Thank you! Now, I will forward this screenshot to the admin."
        )
        return await forward_to_admin_for_acknowledgement(update, context, message_type='image', message=photo, transaction_id=transaction_id)
    else:
        await update.message.reply_text(
            "Please upload a screenshot."
        )
        return PHOTO_REQUESTED

async def forward_to_admin_for_acknowledgement(update: Update, context: ContextTypes.DEFAULT_TYPE, message_type: str, message, transaction_id):
    """
    Send a copy of the message/screenshot to an admin for acknowledgement.
    Users can continue interacting with the bot while waiting for response

    Args:
        update (Update): _description_
        context (ContextTypes.DEFAULT_TYPE): _description_
        message_type (str): 'text' or 'image'
        id: Transaction ID if payment (image) or Job Posts ID if post (text)
    """    
    logger.info(f"get_admin_acknowledgement() called, forwarding a {message_type} to USER {ADMIN_CHAT_ID}")
    if message_type == 'image':
        photo = message
        query_string = f"SELECT chat_id, package_id FROM transactions WHERE transaction_id = '{transaction_id}'"
        results = await get_db(query_string)
        logger.info(f"Results: {results}")
        chat_id, package_id = results[0] # unpack tuple
        # Set callback data from ID provided
        ss_accept_callback_data = f"ss_accept_{transaction_id}" # Callbackdata has fixed format: ss_<transaction_ID> (screenshot) or jp_<transaction_ID> (job post)
        ss_reject_callback_data = f"ss_reject_{transaction_id}"
        logger.info(f"Callback data: {ss_accept_callback_data}, {ss_reject_callback_data}")
        keyboard = [
            [InlineKeyboardButton("Approve", callback_data=ss_accept_callback_data)],
            [InlineKeyboardButton("Reject", callback_data=ss_reject_callback_data)]
        ] # Can check transaction ID if need details
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_photo(
            chat_id=ADMIN_CHAT_ID,
            photo=photo,
            caption=f"Dear Admin, purchase made by {chat_id} for Package {package_id}", #TODO add details regarding transaction
            reply_markup=reply_markup
        )
        # pass
        await update.message.reply_text(
        "Photo forwarded to admin."
    )
    else:
        await update.message.reply_text(
            "No screenshot submission found."
        )
    # End the conversation
    return ConversationHandler.END


async def get_admin_acknowledgement(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    CallbackHandler for the acknowledgement button when messages or screenshots are forwarded to the admin.
    This gets called when the function is pressed
    Callbackdata has fixed format: ss_accept_<ID> / ss_reject_<ID> (screenshot) or jp_accept_<ID> / jp_reject_<ID? (job post)
    Args:   
        update (Update): _description_
        context (ContextTypes.DEFAULT_TYPE): _description_
    """  
    # Get callback query data (e.g. ss_accept_<ID>)
    logger.info("Approve/Reject button pressed")
    query = update.callback_query
    logger.info(f'Callback query data: {query.data}')
    query_data = query.data
    # Check which query data it is (which button admin pressed)
    if query_data.startswith('ss_'): 
        logger.info("SS query found")
        # Getting admin response as well as transaction ID
        status, transaction_id = query_data.split('_')[1:]
        # Select chat_id based on transaction_id
        query_string = f"SELECT chat_id FROM transactions WHERE transaction_id = '{transaction_id}'"
        results = await get_db(query_string)
        logger.info(f"Results: {results}")
        chat_id = results[0][0]
        # # Get chat id from agency id
        # query_string = f"SELECT chat_id FROM agencies WHERE id = '{agency_id}'"
        # results = await get_db(query_string)
        # chat_id = results[0][0]

        if status == 'accept':
            # Update transaction entry status to 'Approved'
            query_string = f"UPDATE transactions SET status = 'Approved' WHERE transaction_id = '{transaction_id}'"
            await set_db(query_string)
            logger.info(f"Approved {transaction_id} in database!")
            await query.answer()  # Acknowledge the callback query to remove the loading state

            
            # Edit the caption of the photo message
            await query.edit_message_caption(caption="You have acknowledged the screenshot.\nCredits have been transferred.")
            
            # Notify the user
            await context.bot.send_message(chat_id=chat_id, text="Your payment has been acknowledged by an admin!.")
        
        elif status == 'reject':
            # Update transaction entry status to 'Rejected'
            query_string = f"UPDATE transactions SET status = 'rejected' WHERE transaction_id = '{transaction_id}'"
            await set_db(query_string)
            logger.info(f"Rejected {transaction_id} in database!")
            await query.answer()  # Acknowledge the callback query to remove the loading state

            
            # Edit the caption of the photo message
            await query.edit_message_caption(caption="You have rejected the screenshot.\nUser will be notified")
            
            # Notify the user
            await context.bot.send_message(chat_id=chat_id, text="Your payment has been rejected by an admin. Please PM admin for more details")






# async def forward_photo_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     logger.info("LOG: forward_photo_to_admin() called")
#     if 'photo' in context.user_data:
#         # Forward photo to admin
#         photo = context.user_data['photo']
#         logger.info(f"CHAT ID: {update.message.chat.id}, TYPE: {type(update.message.chat.id)}")
#         # Create an inline keyboard button for acknowledgment with user chat ID in the callback data
#         keyboard = [[InlineKeyboardButton("Acknowledge", callback_data=str(update.message.chat.id))]]
#         reply_markup = InlineKeyboardMarkup(keyboard)
#         await context.bot.send_photo(
#             chat_id=ADMIN_CHAT_ID,
#             photo=photo,
#             caption="Dear admin, please acknowledge this photo", #TODO add details regarding transaction
#             reply_markup=reply_markup
#         )

#         await update.message.reply_text(
#             "Photo forwarded to admin."
#         )
#     else:
#         await update.message.reply_text(
#             "No screenshot submission found."
#         )
#     # End the conversation
#     return ConversationHandler.END
    

# async def admin_acknowledge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
#     logger.info(f"Update: {update}")
#     logger.info(f"Context USER DATA: {context.user_data}")

#     query = update.callback_query
#     logger.info(f'Query data: {query.data}')
#     await query.answer()  # Acknowledge the callback query to remove the loading state

#     # Get transaction details
#     # agency_id = myTransaction.agency_id

    
#     # Edit the caption of the photo message
#     await query.edit_message_caption(caption="You have acknowledged the photo.")
    
#     # Notify the user
#     await context.bot.send_message(chat_id=query.data, text="Your payment has been acknowledged by an admin!.")

###########################################################################################################################################################   

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""
    user = update.message.from_user
    logger.info("User %s canceled the conversation.", user.first_name)
    await update.message.reply_text(
        "Bye! I hope we can talk again some day.", reply_markup=ReplyKeyboardRemove()
    )

    return ConversationHandler.END

###########################################################################################################################################################   
# Delete profile
async def delete_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_handle = update.effective_user.username
    logger.info(user_handle)

    # Retrieve agency and applicant profiles for the user_handle
    async with AsyncSessionLocal() as conn:
        # Execute the query and fetch all results
        results = await conn.execute(
            sqlalchemy.text(
               f"SELECT id,agency_name FROM agencies WHERE user_handle = '{user_handle}'"
               )
        )
        agency_profiles = results.fetchall()
        logger.info(agency_profiles)

        results = await conn.execute(
            sqlalchemy.text(
                f"SELECT id,name FROM applicants WHERE user_handle = '{user_handle}'"
                )
        )
        applicant_profiles = results.fetchall()

    # Format profiles as inline buttons
    keyboard = []
        
    for id, agency_name in agency_profiles:
        logger.info(id)
        logger.info(agency_name)
        keyboard.append([InlineKeyboardButton(f"Agency - {agency_name}", callback_data=f"delete|agency|{id}")])
    
    
    for id, applicant_name in applicant_profiles:
        logger.info(id)
        logger.info(applicant_name)
        keyboard.append([InlineKeyboardButton(f"Applicant - {applicant_name}", callback_data=f"delete|applicant|{id}")])

    # Check if both profiles are empty
    if not agency_profiles and not applicant_profiles:
        await update.message.reply_text('You have no profiles to delete.')
        return
        
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('Select the profile you want to delete:', reply_markup=reply_markup)
    logger.info("exited retrieve function")

# Function to handle button clicks for profile deletion
async def delete_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("entered delete_button_click")
    query = update.callback_query
    await query.answer()
    try:
        # Extracting profile type and name from callback_data
        parts = query.data.split('|')
        action = parts[1]  # First part is the action
        profile_name = parts[2]  # Remaining parts are profile_name
        
        logger.info("profile name but maybe just id: ")    
        logger.info(profile_name)
            
        if action == 'agency':
            async with AsyncSessionLocal() as conn:
                await conn.execute(
                    sqlalchemy.text(
                    f"DELETE FROM agencies WHERE id = '{profile_name}'"
                    )
                )
                await conn.commit()
            
        elif action == 'applicant':
          async with AsyncSessionLocal() as conn:
                await conn.execute(
                    sqlalchemy.text(
                    f"DELETE FROM applicants WHERE id = '{profile_name}'"
                    )
                )
                await conn.commit()

        await query.edit_message_text("Profile deleted successfully!")

    except IndexError:
        # Log the error or handle it as appropriate
        logger.info(f"Error: Malformed callback_data - {query.data}")

    except Exception as e:
        # Log any other unexpected exceptions
        logger.info(f"Unexpected error: {str(e)}")

###########################################################################################################################################################   

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
###########################################################################################################################################################   
# Error Handler
def global_error_handler(update, context):
    """Handles and logs any unexpected errors."""

    # Log the error
    logger.info(f"Update {update} caused error {context.error}")
    traceback.print_exc()

    # Optionally, notify the developer or admin
    context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"An error occurred: {context.error}")





# Bot classes

# Main
async def main() -> None:
    """Set up PTB application and a web application for handling the incoming requests."""
    context_types = ContextTypes(context=CustomContext)
    # Here we set updater to None because we want our custom webhook server to handle the updates
    # and hence we don't need an Updater instance
    # await async_test_db()
    application = (
        Application.builder().token(BOT_TOKEN).updater(None).context_types(context_types).build()
    )

    # Command handlers
    application.add_handler(CommandHandler('create_trans', create_transaction_entry))
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help))
    # application.add_handler(CommandHandler("register", register))
    application.add_handler(CommandHandler("deleteprofile", delete_profile))


    # Payment Convo Handler
    payment_handler = ConversationHandler(
        entry_points=[CommandHandler('verifypayment', verifyPayment)],
        states={
            PHOTO_REQUESTED: [MessageHandler(filters.PHOTO, verifyPayment)]
        },
        
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    application.add_handler(payment_handler)

    # Registration Convo Handler
    registration_conversation_handler = ConversationHandler(
    entry_points=[CommandHandler('register', register)],
    states={
        NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_text_handler)],
        DOB: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_text_handler)],
        PAST_EXPERIENCES: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_text_handler)],
        CITIZENSHIP: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_text_handler)],
        RACE: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_text_handler)],
        GENDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_text_handler)],
        HIGHEST_EDUCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_text_handler)],
        WHATSAPP_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_text_handler)],
        FULL_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_text_handler)],
        COMPANY_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_text_handler)],
        COMPANY_UEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_text_handler)]
    },
    fallbacks=[]
)
    application.add_handler(registration_conversation_handler)
   
# Edit profile convo handler
    edit_profile_handler = ConversationHandler(
        entry_points=[CommandHandler('editprofile', edit_profile)],
        states={
            SELECT_PROFILE: [CallbackQueryHandler(select_profile)],
            SELECT_ATTRIBUTE: [CallbackQueryHandler(select_attribute)],
            ENTER_NEW_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_new_value)],
        },
        fallbacks=[]
    )
    application.add_handler(edit_profile_handler)

# Job post convo handler
    job_post_handler = ConversationHandler(
    entry_points=[CommandHandler('jobpost', job_post)],
    states={
        SELECT_AGENCY: [CallbackQueryHandler(jobpost_button)],
        ENTER_JOB_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, jobpost_text_handler)],
    },
    fallbacks=[]
)
    
    application.add_handler(job_post_handler)
    



    # CallbackQueryHandlers
    application.add_handler(CallbackQueryHandler(delete_button, pattern='^delete\\|'))
    application.add_handler(CallbackQueryHandler(register_button, pattern='^(applicant|agency)$'))
    application.add_handler(CallbackQueryHandler(get_admin_acknowledgement, pattern='^(ss_|jp_)(accept|reject)_\d+$')) #TODO change to trasnaction ID pattern

    # Message Handler
    ## NIL ##

    # Misc
    application.add_handler(TypeHandler(type=WebhookUpdate, callback=webhook_update))
    
    # Error Handlers
    application.add_error_handler(global_error_handler)

    # Pass webhook settings to telegram
    await application.bot.set_webhook(url=f"{URL}/telegram", allowed_updates=Update.ALL_TYPES)

    # Set up webserver
    flask_app = Flask(__name__)

    # Flask app routes
    @flask_app.post("/telegram")  # type: ignore[misc]
    async def telegram() -> Response:
        logger.info(f"MESSAGE RECEIVED: {request.json}")
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