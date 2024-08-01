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
import re
import mysql
import mysql.connector
import asyncio
import schedule
import traceback
import time
import pymysql
from dateutil.relativedelta import relativedelta
from datetime import datetime, timedelta
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
from telegram import Update, Message, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, InputFile
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

# TODO change/sanitize f-string SQL entries to protect from injection attacks - CHANGE TO SAFE FORMAT SO THAT IT WORKS!!!
# TODO add try and except blocks for conversation handlers
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
# ADMIN_CHAT_ID = 494057457 
# ADMIN_CHAT_ID = 566682368
ADMIN_CHAT_ID = 133704697
# ADMIN_CHAT_ID = 1320357219
# ADMIN_CHAT_ID
# CHANNEL_ID = -1002192841091
CHANNEL_ID = -1001496940354
PORT = 8080
BOT_TOKEN = os.environ['BOT_TOKEN'] # nosec B105
JOB_POST_PRICE = 70
PART_JOB_POST_PRICE = 45
JOB_REPOST_PRICE = 30

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
            logger.info(f"Results from query: {data}")
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
            logger.info(f"Results from query: {data}")
            return data
    except Exception as e:
        logger.info(f"Error in interacting with database: {e}")


#SANITIZED VERSION
async def safe_get_db(query_string: str, params: dict = None):
    """
    Retrieves entry from DB
    Example usage:
    query = "SELECT * FROM users WHERE id = :user_id"
    params = {"user_id": 1}
    await get_db(query, params)

    Args:
        query_string (str): Query for DB to execute
        params (dict): Parameters for the query

    Returns:
        Data from query
    """    
    try: 
        logger.info(f"Executing fetch query: {query_string} with params: {params}")
        async with AsyncSessionLocal() as conn:
            results = await conn.execute(sqlalchemy.text(query_string), params)
            data = results.fetchall()
            logger.info(f"Results from query: {data}")
            return data
    except Exception as e:
        logger.info(f"Error in interacting with database: {e}")

logger = logging.getLogger(__name__)

async def safe_set_db(query_string: str, params: dict = None):
    """
    Executes a database commit operation
    Example usage:
    query = "UPDATE users SET name = :name WHERE id = :user_id"
    params = {"name": "John Doe", "user_id": 1}
    await set_db(query, params)

    Args:
        query_string (str): Query for DB to execute
        params (dict): Parameters for the query

    Returns:
        bool: True if the operation was successful, False otherwise
    """
    try:
        logger.info(f"Executing commit query: {query_string} with params: {params}")
        async with AsyncSessionLocal() as conn:
            await conn.execute(sqlalchemy.text(query_string), params)
            await conn.commit()
            return True
    except Exception as e:
        logger.error(f"Error in interacting with database: {e}")
        return False

async def set_db(query_string: str):
    try:
        logger.info(f"Executing commit query: {query_string}")
        async with AsyncSessionLocal() as conn:
            await conn.execute(sqlalchemy.text(query_string))
            await conn.commit()
            return True
    except Exception as e:
        logger.info(f"Error in interacting with database: {e}")
        return False
    
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
# Help command
async def help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    '''Displays help messages'''
    payload_url = html.escape(f"{URL}/submitpayload?user_id=<your user id>&payload=<payload>")
    await update.message.reply_html(
        '''
<u>Commands</u>
Please use the below commands to check on the specific details, if you need help, contact admin @jojoweipop

/start - Starts the bot
/help - Provides help for the bot
/register - Creates new agency or applicant profile
/viewprofile - Displays your agency and applicant profiles
/editprofile - Modifies one of your profiles
/deleteprofile - Delete from a list of your profiles
/purchase_tokens - Top Up account tokens
/purchase_shortlists - Add more shortlist slots!
/purchase_subscription - Top Up account tokens through a subscription plan
/jobpost - Post a new job listing
/jobrepost - Repost a previous job listing
/get_chat_id - Displays your telegram Chat ID (to aid in troubleshooting issues)
/shortlist - Shortlist applicants for your posted jobs
/view_shortlisted - View detailed information of shortlisted candidates
        '''
    )

# Register command
#TODO add error/wrong input filtering/handling

NAME, DOB, PAST_EXPERIENCES, CITIZENSHIP, RACE, GENDER, HIGHEST_EDUCATION, LANGUAGES_SPOKEN, WHATSAPP_NUMBER, FULL_NAME, COMPANY_NAME, COMPANY_UEN = range(12)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Links current tele handle to chat_id
    chat_id = update.effective_chat.id
    user_handle = update.effective_user.name
    query_string = "INSERT INTO user_data (chat_id, user_handle) VALUES (:chat_id, :user_handle) ON DUPLICATE KEY UPDATE user_handle=VALUES(user_handle)"
    params = {"chat_id": chat_id, "user_handle": user_handle}
    await safe_set_db(query_string, params)
    keyboard = [
        [
            InlineKeyboardButton("Applicant", callback_data='applicant'),
            InlineKeyboardButton("Agency", callback_data='agency'),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('Hi, I am SG Part Timers and Talents\' job bot.\nMay I ask if you are a Job Applicant or a Job Poster?', reply_markup=reply_markup)
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
        await query.edit_message_text(text="You chose Applicant.")
        await query.message.reply_text("Please answer the following questions for us to complete your profile for you and send out for the job!")
        await query.message.reply_text("Please enter your full name:")
        context.user_data['registration_step'] = 'name'
        return NAME
    elif query.data == 'agency':
        await query.edit_message_text(text="You chose Agency.")
        await query.message.reply_text("Welcome to SG Part Timers & Talents’ telegram group job poster! We will resolve all your hiring needs, with past credentials of 99.5% hired, database of 45,000 job seekers, talents, influencers, cosplayers, models, emcees, DJs.\n\nIn here, we use the currency token to post jobs & shortlists talents.\nPlease check out our packages to post a job!")
        await query.message.reply_text("Please enter your full name:")
        context.user_data['registration_step'] = 'full_name'
        return FULL_NAME

# Define the functions for each step
def is_valid_date(date_str: str) -> bool:
    try:
        datetime.strptime(date_str, '%Y-%m-%d')
        return True
    except ValueError:
        return False

async def ask_for_dob(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Entered ask_for_dob")

    if context.user_data['registration_step'] == 'name':
        context.user_data['name'] = update.message.text
        await update.message.reply_text('Please enter your date of birth (YYYY-MM-DD):')
        context.user_data['registration_step'] = 'dob'
        return DOB

    return await validate_dob(update, context)

async def validate_dob(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Entered validate_dob")
    dob = update.message.text
    logger.info("Before validation function")
    if is_valid_date(dob):
        logger.info("DOB is valid")
        context.user_data['dob'] = dob
        return await ask_for_lang(update, context)
    else:
        logger.info("Invalid DOB format")
        await update.message.reply_text('Invalid date format. Please enter your date of birth in the format YYYY-MM-DD:')
        return DOB


async def ask_for_lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Entered ask_for_lang")
    context.user_data['dob'] = update.message.text
    await update.message.reply_text('Please enter the languages you speak:')
    context.user_data['registration_step'] = 'lang_spoken'
    return LANGUAGES_SPOKEN

async def ask_for_past_experiences(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Entered ask_for_past_experiences")
    context.user_data['lang_spoken'] = update.message.text
    await update.message.reply_text('Please enter your past experiences to improve chances of getting shortlisted:')
    context.user_data['registration_step'] = 'past_experiences'
    return PAST_EXPERIENCES

async def ask_for_citizenship(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Entered ask_for_citizenship")
    context.user_data['past_experiences'] = update.message.text

    keyboard = [
        [InlineKeyboardButton("Singaporean", callback_data='Singaporean')],
        [InlineKeyboardButton("Permenant Resident(PR)", callback_data='Permenant Resident(PR)')],
        [InlineKeyboardButton("Student Pass", callback_data='Student Pass')],
        [InlineKeyboardButton("Foreign Passport Holder", callback_data='Foreign Passport Holder')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text('Please enter your citizenship status:', reply_markup=reply_markup)
    context.user_data['registration_step'] = 'citizenship'
    return CITIZENSHIP


async def citizenship_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Clicked citizenship button")
    query = update.callback_query
    await query.answer()

    context.user_data['citizenship'] = query.data

     # Move to the next step
    return await ask_for_race(update, context)
    return RACE

async def ask_for_race(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Entered ask_for_race")
    keyboard = [
        [InlineKeyboardButton("Chinese", callback_data='Chinese')],
        [InlineKeyboardButton("Malay", callback_data='Malay')],
        [InlineKeyboardButton("Indian", callback_data='Indian')],
        [InlineKeyboardButton("Eurasian", callback_data='Eurasian')],
        [InlineKeyboardButton("Others", callback_data='Others')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text('Please enter your race:',reply_markup=reply_markup)
    context.user_data['registration_step'] = 'race'
    return RACE


async def race_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Race button clicked")
    query = update.callback_query
    await query.answer()

    context.user_data['race'] = query.data

    # Move to the next step
    return await ask_for_gender(update, context)
    return GENDER


async def ask_for_gender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Entered ask_for_gender")
    keyboard = [
        [
            InlineKeyboardButton("Male", callback_data='male'),
            InlineKeyboardButton("Female", callback_data='female'),
        ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text('Please select your gender:', reply_markup=reply_markup)

    context.user_data['registration_step'] = 'gender'
    return GENDER

async def gender_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Gender button clicked")
    query = update.callback_query
    await query.answer()

    context.user_data['gender'] = query.data

    # Move to the next step
    return await ask_for_highest_education(update, context)
    return HIGHEST_EDUCATION

async def ask_for_highest_education(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Entered ask_for_highest_education")

    keyboard = [
        [InlineKeyboardButton("O-level Graduate", callback_data='O-level Graduate')],
        [InlineKeyboardButton("ITE Graduate", callback_data='ITE Graduate')],
        [InlineKeyboardButton("A-level Graduate", callback_data='A-level Graduate')],
        [InlineKeyboardButton("Diploma Graduate", callback_data='Diploma Graduate')],
        [InlineKeyboardButton("Degree Graduate", callback_data='Degree Graduate')],
        [InlineKeyboardButton("Undergraduate", callback_data='Undergraduate')],
        [InlineKeyboardButton("Studying in Poly/JC", callback_data='Studying in Poly/JC')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text('Please select your highest education:',reply_markup= reply_markup)

    context.user_data['registration_step'] = 'highest_education'
    return HIGHEST_EDUCATION

async def highest_education_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Education button clicked")
    query = update.callback_query
    await query.answer()

    context.user_data['highest_education'] = query.data

    # Move to the next step
    return await ask_for_whatsapp_number(update, context)
    return WHATSAPP_NUMBER


async def ask_for_whatsapp_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Entered ask_for_whatsapp_number")
    
    await  update.callback_query.edit_message_text('Please enter your WhatsApp number:')
    context.user_data['registration_step'] = 'whatsapp_number'
    return WHATSAPP_NUMBER

async def save_applicant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Entered save_applicant")
    context.user_data['whatsapp_number'] = update.message.text
    async with AsyncSessionLocal() as conn:
        await conn.execute(
            sqlalchemy.text(
        f"INSERT INTO applicants (user_handle, chat_id, name, dob, past_exp, citizenship, race, gender, education, lang_spoken, whatsapp_no) VALUES ('{context.user_data['user_handle']}','{context.user_data['chat_id']}', '{context.user_data['name']}', '{context.user_data['dob']}', '{context.user_data['past_experiences']}', '{context.user_data['citizenship']}', '{context.user_data['race']}', '{context.user_data['gender']}', '{context.user_data['highest_education']}', '{context.user_data['lang_spoken']}','{context.user_data['whatsapp_number']}')"

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
        f'''INSERT INTO agencies (user_handle, chat_id, name, agency_name, agency_uen) VALUES ("{context.user_data['user_handle']}", "{context.user_data['chat_id']}", "{context.user_data['full_name']}", "{context.user_data['company_name']}", "{context.user_data['company_uen']}")'''
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
            return await ask_for_lang(update, context)
        elif step == 'lang_spoken':
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
                [InlineKeyboardButton("Company UEN", callback_data='edit_attribute|agency|agency_uen')]
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
                [InlineKeyboardButton("WhatsApp Number", callback_data='edit_attribute|applicant|whatsapp_no')]
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

        if attribute == 'citizenship':
            keyboard = [
                [InlineKeyboardButton("Singaporean", callback_data='Singaporean')],
                [InlineKeyboardButton("Permanent Resident(PR)", callback_data='Permanent Resident(PR)')],
                [InlineKeyboardButton("Student Pass", callback_data='Student Pass')],
                [InlineKeyboardButton("Foreign Passport Holder", callback_data='Foreign Passport Holder')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text('Select Citizenship:', reply_markup=reply_markup)
            return ENTER_NEW_VALUE

        elif attribute == 'race':
            keyboard = [
                [InlineKeyboardButton("Chinese", callback_data='Chinese')],
                [InlineKeyboardButton("Malay", callback_data='Malay')],
                [InlineKeyboardButton("Indian", callback_data='Indian')],
                [InlineKeyboardButton("Eurasian", callback_data='Eurasian')],
                [InlineKeyboardButton("Others", callback_data='Others')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text('Select Race:', reply_markup=reply_markup)
            return ENTER_NEW_VALUE

        elif attribute == 'gender':
            keyboard = [
                [InlineKeyboardButton("Male", callback_data='Male')],
                [InlineKeyboardButton("Female", callback_data='Female')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text('Select Gender:', reply_markup=reply_markup)
            return ENTER_NEW_VALUE

        elif attribute == 'education':
            keyboard = [
                [InlineKeyboardButton("O-level Graduate", callback_data='O-level Graduate')],
                [InlineKeyboardButton("ITE Graduate", callback_data='ITE Graduate')],
                [InlineKeyboardButton("A-level Graduate", callback_data='A-level Graduate')],
                [InlineKeyboardButton("Diploma Graduate", callback_data='Diploma Graduate')],
                [InlineKeyboardButton("Degree Graduate", callback_data='Degree Graduate')],
                [InlineKeyboardButton("Undergraduate", callback_data='Undergraduate')],
                [InlineKeyboardButton("Studying in Poly/JC", callback_data='Studying in Poly/JC')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text('Select Highest Education:', reply_markup=reply_markup)
            return ENTER_NEW_VALUE

        else:
            await query.edit_message_text(f"Please enter the new value for {attribute.replace('_', ' ').title()}:")
            return ENTER_NEW_VALUE

    except IndexError:
        logger.info(f"Error: Malformed callback_data - {query.data}")

    return ConversationHandler.END

# Callback function to handle new value input
async def enter_new_value(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    new_value = query.data if query else update.message.text
    profile_type = context.user_data['edit_profile_type']
    profile_id = context.user_data['edit_profile_id']
    attribute = context.user_data['edit_attribute']

    try:
        if profile_type == 'agency':
            async with AsyncSessionLocal() as conn:
                await conn.execute(
                    sqlalchemy.text(
                        f"UPDATE agencies SET {attribute} = :new_value WHERE id = :profile_id"
                    ),
                    {'new_value': new_value, 'profile_id': profile_id}
                )
                await conn.commit()

        elif profile_type == 'applicant':
            async with AsyncSessionLocal() as conn:
                await conn.execute(
                    sqlalchemy.text(
                        f"UPDATE applicants SET {attribute} = :new_value WHERE id = :profile_id"
                    ),
                    {'new_value': new_value, 'profile_id': profile_id}
                )
                await conn.commit()
        if update.message:
            await update.message.reply_text('Profile updated successfully!')
        if update.callback_query:
            await update.callback_query.message.reply_text('Profile updated successfully!')
        context.user_data.clear()  # Clear user data after successful update

    except Exception as e:
        logger.info(f"Unexpected error: {str(e)}")
        if update.message:
            await update.message.reply_text('An error occurred while updating the profile.')
        if update.callback_query:
            await update.callback_query.message.reply_text('An error occurred while updating the profile.')

    return ConversationHandler.END


###########################################################################################################################################################   
# #Job Posting

SELECT_AGENCY_REPOST, SELECT_JOB_TO_REPOST, CONFIRMATION_JOB_REPOST= range(3)
async def job_repost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    context.user_data['chat_id'] = chat_id
    # Retrieve agency profiles for the user_handle
    query_string = """
    SELECT jp.id AS job_id
    FROM job_posts jp
    JOIN agencies a ON jp.agency_id = a.id
    WHERE jp.status = 'approved' AND a.chat_id = :chat_id
    """
    params = {"chat_id": chat_id}
    results = await safe_get_db(query_string, params)
    previously_posted_jobs = results
    
    # Check for previously posted jobs
    if not previously_posted_jobs:
        await update.message.reply_text('You have no previous job listings.')
        return ConversationHandler.END

    # Format profiles as inline buttons
    await update.message.reply_text('Select the job which you want to repost:')

    for job in previously_posted_jobs:
        job_post_id = job[0]
        query_string = '''
        SELECT EXISTS (
        SELECT 1
        FROM job_posts
        WHERE id = :job_post_id AND status = 'approved')
        '''
        params = {"job_post_id": job_post_id}
        results = await safe_get_db(query_string, params)
        repost = results[0][0]

        # Check if part time
        query_string = '''
        SELECT EXISTS (
        SELECT 1
        FROM job_posts
        WHERE id = :job_post_id AND job_type = 'part')
        '''
        params = {"job_post_id": job_post_id}
        results = await safe_get_db(query_string, params)
        part_time = results[0][0]
        message = await draft_job_post_message(job[0], repost=repost, part_time=part_time)
    
        keyboard = [
            [InlineKeyboardButton(f"Job ID: {job[0]}", callback_data=f"jobrepost|{job[0]}")]
            # for job in previously_posted_jobs
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(text=message,reply_markup=reply_markup, parse_mode='HTML')

    return SELECT_JOB_TO_REPOST

async def jobrepost_button(update: Update, context: CallbackContext) -> int:
    '''
    Handles the selection of job in job_repost()
    Callbackdata should be "jobrepost|<job_id>"
    '''
    query = update.callback_query
    query_data = query.data
    await query.answer()
    context.user_data['repost_job_id'] = query_data.split('|')[1]
    repost_job_id = context.user_data['repost_job_id']
    await query.edit_message_text(f"You have chosen Job {repost_job_id}")
    tokens_to_deduct = JOB_REPOST_PRICE

    # Check if sufficient tokens in balance
    chat_id = context.user_data['chat_id']
    have_enough = await check_sufficient_tokens(update, context, chat_id, tokens_to_deduct)
    if have_enough:
        # Keyboard for callback
        keyboard = []
        confirm_button = [InlineKeyboardButton("Proceed", callback_data="confirm_job_repost")]
        keyboard.append(confirm_button)
        cancel_button = [InlineKeyboardButton("Cancel", callback_data="cancel_job_repost")]
        keyboard.append(cancel_button)
        reply_markup = InlineKeyboardMarkup(keyboard)
        # Send confirmation message
        if update.callback_query:
            await update.callback_query.message.reply_text(text= f"This will cost {tokens_to_deduct} tokens, do you want to proceed with reposting?", reply_markup = reply_markup)
        if update.message:
            await update.message.reply_text(text= f"This will cost {tokens_to_deduct} tokens, do you want to proceed with reposting?", reply_markup = reply_markup)
        return CONFIRMATION_JOB_REPOST
    else:
        if update.callback_query:
            await update.callback_query.message.reply_text(text= "You do not have sufficient tokens.\nPlease top up via /purchase_tokens command!")
        if update.message:
            await update.message.reply_text(text= "You do not have sufficient tokens.\nPlease top up via /purchase_tokens command!")
        return ConversationHandler.END

async def confirm_job_repost(update, context):
    '''
    Handler for confirm job repost button
    '''
    tokens_to_deduct = JOB_REPOST_PRICE
    query = update.callback_query
    chat_id = context.user_data['chat_id']
    # Check if got entry in token_balance table
    query_string = "SELECT EXISTS (SELECT 1 FROM token_balance WHERE chat_id = :chat_id)"
    params = {"chat_id": chat_id}
    results = await safe_get_db(query_string, params)
    have_entry = results[0][0]
    # There is an entry in the table
    if have_entry: 
        # Get balance from chat_id in token_balance table
        query_string = "SELECT tokens FROM token_balance WHERE chat_id = :chat_id"
        results = await safe_get_db(query_string, params)
        token_balance = results[0][0]
        # Check user's account balance
        if (token_balance >= tokens_to_deduct): # Sufficient tokens within account
            new_balance = token_balance - tokens_to_deduct
            # confirmation_msg = await contex.bot.send_message(chat_id = chat_id, text=f"{action} will cost you {tokens_to_deduct} tokens.\nYou currently have {token_balance} tokens in your account.\n\nWould you like to continue? ")
            # confirmation_msg_id = confirmation_msg.message_id
            # Get user confirmation to continue

            # Update new balance in token_balance
            query_string = "UPDATE token_balance SET tokens = :new_balance WHERE chat_id = :chat_id"
            params = {"new_balance": new_balance, "chat_id": chat_id}
            if (await safe_set_db(query_string, params)):
                # Repost job
                job_id = context.user_data['repost_job_id']
                # job_id = await save_jobpost(context.user_data)
                message = await draft_job_post_message(job_id, repost=True)
                await forward_to_admin_for_acknowledgement(update, context, message=message, job_post_id = job_id)
                await query.answer()
                logger.info(f"{tokens_to_deduct} tokens have been deducted from {chat_id}'s account")
                await update.callback_query.message.edit_text(
                    f"Purchase successful!\n\n"
                    f"You have <b>{new_balance} tokens</b> remaining.",
                    parse_mode='HTML'
                )
                return ConversationHandler.END
            else:
                logger.info("Issue with token deduction")
        else:
            return ConversationHandler.END
            # return (False, 0)# Insufficient tokens
    else:
        return ConversationHandler.END

###########################################################################################################################################################   
# Job Posting fn

SELECT_AGENCY, ENTER_JOB_DETAILS, CONFIRMATION_JOB_POST, ENTER_JOB_TYPE, ENTER_OTHER_REQ= range(5)
# Function to start job posting
async def post_a_job_button(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    chat_id = query.from_user.id
    # Check if user has chat_id in /start
    query_string = '''
    SELECT EXISTS (
    SELECT 1
    FROM user_data
    WHERE chat_id = :chat_id)
    '''
    params = {"chat_id": chat_id}
    results = await safe_get_db(query_string, params)
    previously_used = results[0][0]
    if previously_used:
        await context.bot.send_message(chat_id=chat_id, text="You can use the /jobpost command to create a job posting!\n\nPlease ensure you have an <b>agency</b> profile before doing so.\nYou can create one with the /register command!", parse_mode='HTML')
    else:
        message = '''
        Welcome to SG Part Timers & Talents’ telegram group job poster! We will resolve all your hiring needs, with past credentials of 99.5% hired, database of 45,000 job seekers, talents, influencers, cosplayers, models, emcees, DJs.\n\n
In here, we use the currency token to post jobs & shortlists talents
Please check out our packages to post a job!'''
        await context.bot.send_message(chat_id=chat_id, text=message)
    await query.answer()
    # # Below should be same/similar to job_post()
    # query_string = "SELECT id, name, agency_name FROM agencies WHERE chat_id = :chat_id"
    # params = {"chat_id": chat_id}
    # agency_profiles = await safe_get_db(query_string, params)

    # if not agency_profiles:
    #     await context.bot.send_message(chat_id=chat_id, text='You have no agency profiles to post a job from.')
    #     return ConversationHandler.END

    # # Format profiles as inline buttons
    # keyboard = [
    #     [InlineKeyboardButton(f"{agency[1]} - {agency[2]}", callback_data=f"jobpost|{agency[0]}")]
    #     for agency in agency_profiles
    # ]

    # reply_markup = InlineKeyboardMarkup(keyboard)
    # await context.bot.send_message(chat_id=chat_id, text='Select the profile you want to use for job posting:', reply_markup=reply_markup)

    # return SELECT_AGENCY

async def job_post(update: Update, context: CallbackContext) -> int:
    # user_handle = update.effective_user.username
    chat_id = update.effective_chat.id
    # Retrieve agency profiles for the user_handle
    async with AsyncSessionLocal() as conn:
        # Execute the query and fetch all results
        results = await conn.execute(
            sqlalchemy.text(
               f"SELECT id, name, agency_name FROM agencies WHERE chat_id = '{chat_id}'"
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
    logger.info("jobpost_button pressed")
    query = update.callback_query
    logger.info("jobpost button:" + query.data)
    await query.answer()
    context.user_data['agency_id'] = query.data.split('|')[1]
     # Ask for job type
    keyboard = [
        [InlineKeyboardButton("Full Time", callback_data="job_type_full_time")],
        [InlineKeyboardButton("Part Time", callback_data="job_type_part_time")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text('Please select the type of job:', reply_markup=reply_markup)

    return ENTER_JOB_TYPE

# Callback function to handle job type selection
async def job_type_selection(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "job_type_full_time":
        context.user_data['jobpost_job_type'] = 'full'
    elif query.data == "job_type_part_time":
        context.user_data['jobpost_job_type'] = 'part'

    context.user_data['jobpost_step'] = 'job_title'
    await query.edit_message_text('Please enter the Job Title:')

    return ENTER_JOB_DETAILS


async def check_sufficient_tokens(update, context, chat_id, tokens_to_deduct):
    chat_id = context.user_data['chat_id']
    # Check if got entry in token_balance table
    query_string = "SELECT EXISTS (SELECT 1 FROM token_balance WHERE chat_id = :chat_id)"
    params = {"chat_id": chat_id}
    results = await safe_get_db(query_string, params)
    have_entry = results[0][0]
    # There is an entry in the table
    if have_entry: 
        # Get balance from chat_id in token_balance table
        query_string = "SELECT tokens FROM token_balance WHERE chat_id = :chat_id"
        results = await safe_get_db(query_string, params)
        token_balance = results[0][0]
        # Check user's account balance
        if (token_balance >= tokens_to_deduct): # Sufficient tokens within account
            return True
        else: return False
    else: return False

async def confirm_job_post(update, context):
    '''
    Handler for confirm button
    '''
    tokens_to_deduct = JOB_POST_PRICE
    # if part time job
    if context.user_data['jobpost_job_type'] == 'part':
        tokens_to_deduct = PART_JOB_POST_PRICE
        part_time = True
    else:
        tokens_to_deduct = JOB_POST_PRICE
        part_time = False
    query = update.callback_query
    chat_id = context.user_data['chat_id']
    # Check if got entry in token_balance table
    query_string = "SELECT EXISTS (SELECT 1 FROM token_balance WHERE chat_id = :chat_id)"
    params = {"chat_id": chat_id}
    results = await safe_get_db(query_string, params)
    have_entry = results[0][0]
    # There is an entry in the table
    if have_entry: 
        # Get balance from chat_id in token_balance table
        query_string = "SELECT tokens FROM token_balance WHERE chat_id = :chat_id"
        results = await safe_get_db(query_string, params)
        token_balance = results[0][0]
        # Check user's account balance
        if (token_balance >= tokens_to_deduct): # Sufficient tokens within account
            new_balance = token_balance - tokens_to_deduct
            # confirmation_msg = await contex.bot.send_message(chat_id = chat_id, text=f"{action} will cost you {tokens_to_deduct} tokens.\nYou currently have {token_balance} tokens in your account.\n\nWould you like to continue? ")
            # confirmation_msg_id = confirmation_msg.message_id
            # Get user confirmation to continue

            # Update new balance in token_balance
            query_string = "UPDATE token_balance SET tokens = :new_balance WHERE chat_id = :chat_id"
            params = {"new_balance": new_balance, "chat_id": chat_id}
            if (await safe_set_db(query_string, params)):
                job_id = await save_jobpost(context.user_data)
                message = await draft_job_post_message(job_id, part_time=part_time)
                await forward_to_admin_for_acknowledgement(update, context, message=message, job_post_id = job_id)
                await query.answer()
                logger.info(f"{tokens_to_deduct} tokens have been deducted from {chat_id}'s account")
                await update.callback_query.message.edit_text(
                    f"Purchase successful!\n\n"
                    f"You have <b>{new_balance} tokens</b> remaining.",
                    parse_mode='HTML'
                )
                return ConversationHandler.END
            else:
                logger.info("Issue with token deduction")
        else:
            return ConversationHandler.END
            # return (False, 0)# Insufficient tokens
    else:
        return ConversationHandler.END
        # return (False, 0) # No entry in token_balance
async def cancel_job_post(update, context):
    callback_query = update.callback_query
    await callback_query.answer()
    await callback_query.message.edit_text("Job Post cancelled!")
    logger.info("CANCELLED!")
    return ConversationHandler.END

async def cancel_job_repost(update, context):
    callback_query = update.callback_query
    await callback_query.answer()
    await callback_query.message.edit_text("Job Repost cancelled!")
    logger.info("CANCELLED!")
    return ConversationHandler.END


async def spend_tokens(update: Update, context: CallbackContext, chat_id, tokens_to_deduct, action: str):
    """
    1. Checks user's account balance
    2a. If enough, show cost of action, user's current balance, and new balance (This cost X tokens, You have Y tokens, this will leave you with Z tokens. Would you like to continue?)
    2b. If not enough, show cost of action, user's current balance and ask to top up. (This cost X tokens, unfortunately you only have Y tokens, please top up tokens through the /purchastokens command and try again)
    3a. (From 2a) Deduct and return True
    3b. (From 2b) Return False

    Tells user how much an action will cost.
    Checks if user has enough in account
    If there is enough, request confirmation from user to spend 
    Deducts number of tokens from chat_id in token_balance table of DB.
    Checks if there are enough tokens first to spend. If not enough, returns False and suggests the /purchastokens command to user.
    If there is enough, returns True

    Args:
        chat_id (_type_): Chat ID spending tokens
        tokens_to_deduct (_type_): number of tokens to deduct/spend
        action (str): Action that costs tokens, this will be sent to the user (e.g. Broadcasting this job offer)

    Returns:
        (bool, int)
        bool: True if deduction went through, False otherwise.
        int: Balance of account
    """    
    # Check if got entry in token_balance table
    query_string = "SELECT EXISTS (SELECT 1 FROM token_balance WHERE chat_id = :chat_id)"
    params = {"chat_id": chat_id}
    results = await safe_get_db(query_string, params)
    have_entry = results[0][0]
    # There is an entry in the table
    if have_entry: 
        # Get balance from chat_id in token_balance table
        query_string = "SELECT tokens FROM token_balance WHERE chat_id = :chat_id"
        results = await safe_get_db(query_string, params)
        token_balance = results[0][0]
        # Check user's account balance
        if (token_balance >= tokens_to_deduct): # Sufficient tokens within account
            new_balance = token_balance - tokens_to_deduct
            # confirmation_msg = await contex.bot.send_message(chat_id = chat_id, text=f"{action} will cost you {tokens_to_deduct} tokens.\nYou currently have {token_balance} tokens in your account.\n\nWould you like to continue? ")
            # confirmation_msg_id = confirmation_msg.message_id
            # Get user confirmation to continue

            # Update new balance in token_balance
            query_string = "UPDATE token_balance SET tokens = :new_balance WHERE chat_id = :chat_id"
            params = {"new_balance": new_balance, "chat_id": chat_id}
            if (await safe_set_db(query_string, params)):
                logger.info(f"{tokens_to_deduct} tokens have been deducted from {chat_id}'s account")
                return (True, new_balance)
            else:
                logger.info("Issue with token deduction")
        else:
            return (False, 0)# Insufficient tokens
    else:
        return (False, 0) # No entry in token_balance
       
# Callback function to handle job posting details input
async def jobpost_text_handler(update: Update, context: CallbackContext) -> int:
    text = update.message.text
    context.user_data['chat_id'] = update.effective_chat.id
    if 'jobpost_step' in context.user_data:
        step = context.user_data['jobpost_step']

        if step == 'job_title':
            context.user_data['jobpost_job_title'] = text
            await update.message.reply_text('Please specify the Company this job belongs to:')
            context.user_data['jobpost_step'] = 'company'
            return ENTER_JOB_DETAILS

        elif step == 'company':
            context.user_data['jobpost_company'] = text
            await update.message.reply_text('Please provide the Industry for this job:')
            context.user_data['jobpost_step'] = 'industry'
            return ENTER_JOB_DETAILS

        elif step == 'industry':
            context.user_data['jobpost_industry'] = text
            await update.message.reply_text('Please provide the Date(s) for this job opportunity:')
            context.user_data['jobpost_step'] = 'date'
            return ENTER_JOB_DETAILS

        elif step == 'date':
            context.user_data['jobpost_date'] = text
            await update.message.reply_text('Please provide the Time for this job opportunity:')
            context.user_data['jobpost_step'] = 'time'
            return ENTER_JOB_DETAILS

        elif step == 'time':
            context.user_data['jobpost_time'] = text
            await update.message.reply_text('Please state the Basic Salary for this job:')
            context.user_data['jobpost_step'] = 'salary'
            return ENTER_JOB_DETAILS
        
        elif step == 'salary':
            context.user_data['jobpost_basic_salary'] = text
            await update.message.reply_text('Please state the Commissions and Targets for this job:')
            context.user_data['jobpost_step'] = 'commission'
            return ENTER_JOB_DETAILS

        elif step == 'commission':
            context.user_data['jobpost_commission'] = text
            await update.message.reply_text('Please describe the Job Scope and responsibilities:')
            context.user_data['jobpost_step'] = 'job_scope'
            return ENTER_JOB_DETAILS

        elif step == 'job_scope':
            logger.info("ENTERED JOB SCOPE ELIF")
            context.user_data['jobpost_job_scope'] = text
            await update.message.reply_text(
                'Do you have any additional requirements for this job? If none, please click "No".',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Yes", callback_data="yes")],
                    [InlineKeyboardButton("No", callback_data="no")]
                ])
            )
            context.user_data['jobpost_step'] = 'additional_req'
            return ENTER_OTHER_REQ
          
    return ENTER_JOB_DETAILS
  
            



# Callback function to handle additional requirements prompt
async def jobpost_additional_req(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "no":
        context.user_data['jobpost_other_req'] = 'none'
        context.user_data['jobpost_step'] = 'completed'

        context.user_data['chat_id'] = update.effective_chat.id
        chat_id = context.user_data['chat_id']
        # job_id = await save_jobpost(context.user_data)
        tokens_to_deduct = JOB_POST_PRICE
        if context.user_data['jobpost_job_type'] == 'part':
            tokens_to_deduct = PART_JOB_POST_PRICE
        # Check if enough tokens in balance
        have_enough = await check_sufficient_tokens(update, context, chat_id, tokens_to_deduct)
        if have_enough:
            # Keyboard for callback
            keyboard = []
            confirm_button = [InlineKeyboardButton("Proceed", callback_data="confirm_job_post")]
            keyboard.append(confirm_button)
            cancel_button = [InlineKeyboardButton("Cancel", callback_data="cancel_job_post")]
            keyboard.append(cancel_button)
            reply_markup = InlineKeyboardMarkup(keyboard)
            # Send confirmation message
            await update.callback_query.message.reply_text(text= f"This will cost {tokens_to_deduct} tokens, do you want to proceed with posting?", reply_markup = reply_markup)
            return CONFIRMATION_JOB_POST
        else:
            await update.callback_query.message.reply_text(text= "You do not have sufficient tokens.\nPlease top up via /purchase_tokens command!")
            return ConversationHandler.END

    elif query.data == "yes":
        await query.message.reply_text("Please specify your additional requirements:")
        context.user_data['jobpost_step'] = 'other_req_text'
        return ENTER_OTHER_REQ

    return ENTER_OTHER_REQ


# Callback function to handle the text input for additional requirements
async def handle_other_req_text(update: Update, context: CallbackContext) -> int:
    text = update.message.text
    context.user_data['jobpost_other_req'] = text
    context.user_data['jobpost_step'] = 'completed'
    chat_id = context.user_data['chat_id']
#     job_id = await save_jobpost(context.user_data)
    tokens_to_deduct = JOB_POST_PRICE
    if context.user_data['jobpost_job_type'] == 'part':
        tokens_to_deduct = PART_JOB_POST_PRICE
    # Check if enough tokens in balance
    have_enough = await check_sufficient_tokens(update, context, chat_id, tokens_to_deduct)
    if have_enough:
        # Keyboard for callback
        keyboard = []
        confirm_button = [InlineKeyboardButton("Proceed", callback_data="confirm_job_post")]
        keyboard.append(confirm_button)
        cancel_button = [InlineKeyboardButton("Cancel", callback_data="cancel_job_post")]
        keyboard.append(cancel_button)
        reply_markup = InlineKeyboardMarkup(keyboard)
        # Send confirmation message
        if update.callback_query:
            await update.callback_query.message.reply_text(text= f"This will cost {tokens_to_deduct} tokens, do you want to proceed with posting?", reply_markup = reply_markup)
        if update.message:
            await update.message.reply_text(text= f"This will cost {tokens_to_deduct} tokens, do you want to proceed with posting?", reply_markup = reply_markup)
        return CONFIRMATION_JOB_POST
    else:
        if update.callback_query:
            await update.callback_query.message.reply_text(text= "You do not have sufficient tokens.\nPlease top up via /purchase_tokens command!")
        if update.message:
            await update.message.reply_text(text= "You do not have sufficient tokens.\nPlease top up via /purchase_tokens command!")
        return ConversationHandler.END

    return ENTER_JOB_DETAILS

async def draft_job_post_message(job_id, repost=False, part_time=False) -> str:
    """    
    Generates job post id to be approved by admin and posted in channel later on.
    Args:
        job_id (_type_): Job Post ID, returned by save_jobpost
        repost: If the job is a repost, attaches a repost tag if it is

    Returns:
        str: Message to be approved by admin
    """    
    # Fetch job details from db
    query_string = f"SELECT agency_id, job_type, company_name, industry, job_title, date, time, basic_salary, commissions, job_scope, other_req FROM job_posts WHERE id = '{job_id}'"
    results = await get_db(query_string)
    agency_id, job_type, company_name, industry, job_title, date, time, basic_salary, commissions, job_scope, other_req= results[0]
    # Fetch agency details from agency_id
    query_string = f"SELECT user_handle, chat_id, name, agency_name, agency_uen FROM agencies WHERE id = '{agency_id}'"
    results = await get_db(query_string)
    user_handle, chat_id, name, agency_name, agency_uen = results[0]
    # Draft message template
    repost_prefix = "<b>[REPOST]</b>"
    part_time_tag = "<b>[PART-TIME]</b>"
    full_time_tag = "<b>[FULL-TIME]</b>"
    tag = ''
    if (job_type == 'part'):
        part_time = True
    else:
        part_time = False
    if part_time:
        tag = part_time_tag
    else:
        tag = full_time_tag

    message = f'''
<b><u>Job Post [ID: {job_id}]</u></b>\n{tag}\n
<b>Company Name</b>:\n{company_name}\n
<b>Industry</b>:\n{industry}\n
<b>Job Title</b>:\n{job_title}\n
<b>Date</b>:\n{date}\n
<b>Time</b>:\n{time}\n
<b>Basic Salary</b>:\n{basic_salary}\n
<b>Commissions & Targets</b>:\n{commissions}\n
<b>Job Scope</b>:\n{job_scope}\n
'''
    if repost:
        message = repost_prefix + message
    if other_req != "none":
        message += f"<b>Additional Requirements</b>:\n{other_req}\n\n"
    return message


async def post_job_in_channel(update: Update, context: ContextTypes.DEFAULT_TYPE, message, job_post_id):
    """
    Broadcasts the job in the channel.
    Message will contain a button for applicants to press which opens up a private chat from the bot to choose applicant profile.
    Args:
        update (Update): _description_
        context (ContextTypes.DEFAULT_TYPE): _description_
        job_id (_type_): ID of job being broadcasted
    """    
    keyboard = []
    apply_button = [InlineKeyboardButton("Apply", callback_data=f"apply_{job_post_id}")]
    keyboard.append(apply_button)
    post_a_job_button = [InlineKeyboardButton("Post a Job", callback_data="post_a_job")]
    keyboard.append(post_a_job_button)
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id=CHANNEL_ID, text=message, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

async def apply_button_handler(update: Update, context:ContextTypes.DEFAULT_TYPE):
    """
    Should be called when apply button is clicked, pattern = apply_<
    Handles the apply button for applicants when they are interested in a job posting
    """    
    query = update.callback_query
    query_data = query.data
    if query_data.startswith("apply_"):
        job_post_id = query_data.split('_')[1]
    chat_id = query.from_user.id
    logger.info(f"Apply button clicked by {chat_id}")
    # Choose applicant profile to apply for job
    query_string = f'''SELECT id,name FROM applicants WHERE chat_id = "{chat_id}"'''
    results = await get_db(query_string)
    applicant_profiles = results
    keyboard = []
    if not applicant_profiles:
        await context.bot.send_message(chat_id=chat_id, text="You have no applicant profiles to apply for a job.\nYou can create one with the /register command!")

    else:
        for applicant_id, applicant_name in applicant_profiles:
            logger.info(applicant_id)
            logger.info(applicant_name)
            keyboard.append([InlineKeyboardButton(f"Applicant - {applicant_name}", callback_data=f"ja_{job_post_id}_{applicant_id}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(chat_id=chat_id, text=f"You are applying for Job ID {job_post_id}\n\nSelect the applicant profile you want to apply with:", reply_markup=reply_markup)

async def select_applicant_apply(update: Update, context:ContextTypes.DEFAULT_TYPE):
    '''
    Called when applicant picks a profile within apply_button_handler
    Handles selection of applicant profile for applying for a job posting
    '''
    # Get applicant UUID from callback query data
    query = update.callback_query
    query_data = query.data
    if query_data.startswith("ja_"):
        logger.info("Applicant ID found")
        job_post_id, applicant_id = query_data.split('_')[1:]
    query_string = "SELECT name FROM applicants WHERE id = :applicant_id"
    params = {"applicant_id": applicant_id}
    results = await safe_get_db(query_string, params)
    applicant_name = results[0][0]
    # Add applicant id to job application list
    query_string = "INSERT INTO job_applications (applicant_id, job_id, shortlist_status) VALUES (:applicant_id, :job_post_id, 'no')"
    params = {"applicant_id": applicant_id, "job_post_id": job_post_id}
    if await safe_set_db(query_string, params):
        logger.info("Inserted applicant into job post list")
    # Inform agency of new applicant
    # Get agency_id from job_post_id
    query_string = "SELECT agency_id, company_name, job_title FROM job_posts WHERE id = :id"
    params = {"id": job_post_id}
    results = await safe_get_db(query_string, params)
    agency_id, company_name, job_title = results[0]
    # Get chat_id from agency_id
    query_string = "SELECT chat_id FROM agencies WHERE id = :id"
    params = {"id": agency_id}
    results = await safe_get_db(query_string, params)
    if not results:
        await query.edit_message_text("Sorry, that agency no longer exists")
    else:
        chat_id = results[0][0]
        await context.bot.send_message(chat_id=chat_id, text=f"An applicant has applied for Job {job_post_id}: {company_name} - {job_title}.\nPlease use the /shortlist command to shortlist applicants")
        await query.edit_message_text(text=f"{applicant_name} has successfully applied for Job {job_post_id}: {company_name} - {job_title}!")



async def save_jobpost(user_data):
    '''
    Saves job in DB and returns ID of new entry
    '''
    # logger.info(f"QUERY: INSERT INTO job_posts (agency_id, job_title, company_industry, date_time, pay_rate, job_scope, shortlist) VALUES ('{user_data['agency_id']}', '{user_data['jobpost_job_title']}', '{user_data['jobpost_company_industry']}', '{user_data['jobpost_date_time']}', '{user_data['jobpost_pay_rate']}', '{user_data['jobpost_job_scope']}', '0')")
    async with AsyncSessionLocal() as conn:
        result = await conn.execute(
            sqlalchemy.text(
        f"INSERT INTO job_posts (agency_id, job_type, company_name, industry, job_title, date, time, basic_salary, commissions, job_scope, other_req, shortlist) VALUES ('{user_data['agency_id']}','{user_data['jobpost_job_type']}', '{user_data['jobpost_company']}', '{user_data['jobpost_industry']}', '{user_data['jobpost_job_title']}', '{user_data['jobpost_date']}', '{user_data['jobpost_time']}','{user_data['jobpost_basic_salary']}' ,'{user_data['jobpost_commission']}','{user_data['jobpost_job_scope']}','{user_data['jobpost_other_req']}', '0')"
    )
        )
        await conn.commit()
        result = await conn.execute(sqlalchemy.text("SELECT LAST_INSERT_ID()"))
        job_id = result.scalar_one()
        return job_id

###########################################################################################################################################################   
# Purchasing shortlists

CHECK_BALANCE, CHOOSE_AMOUNT, CONFIRM_PURCHASE = range(3)


# Function to handle /purchase_shortlists command
async def purchase_shortlists(update: Update, context: CallbackContext) -> int:
    logger.info("Entered purchase_shortlists function")
    chat_id = update.effective_chat.id

    # Retrieve tokens and shortlists balance from the database
    query_tokens = "SELECT tokens FROM token_balance WHERE chat_id = :chat_id"
    tokens_result = await safe_get_db(query_tokens, {"chat_id": chat_id})
    tokens = tokens_result[0][0] if tokens_result else 0

    query_shortlists = "SELECT shortlist FROM shortlist_balance WHERE chat_id = :chat_id"
    shortlists_result = await safe_get_db(query_shortlists, {"chat_id": chat_id})
    
    # Check if entry for chat_id in shortlist_balance table
    context.user_data['entry_present'] = 1 if shortlists_result else 0

    # Set shortlist number to 0 if no entry present in shortlist_balance table
    shortlists = shortlists_result[0][0] if shortlists_result else 0

    # Display current balances to the user
    message = f"You currently have:\n{tokens} tokens\n{shortlists} shortlists"

    if tokens < 5:
        if update.message:
            await update.message.reply_text(
                message + "\n\n<b>Each 3 shortlists cost 5 tokens.</b>\n\nYou have insufficient tokens. Purchase more tokens at /purchase_tokens.", parse_mode='HTML'
            )
        if update.callback_query:
            await update.callback_query.message.reply_text(
                message + "\n\n<b>Each 3 shortlists cost 5 tokens.</b>\n\nYou have insufficient tokens. Purchase more tokens at /purchase_tokens.", parse_mode='HTML'
            )
        return ConversationHandler.END

    # Ask user how many shortlists they want to buy
    keyboard = [
        [InlineKeyboardButton("Cancel", callback_data="shortlist_cancel_purchase")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        await update.message.reply_text(
            message + "\n\n<b>Each 3 shortlists cost 5 tokens.</b>\n\nHow many shortlists would you like to buy? Please enter a multiple of 3.", parse_mode='HTML', reply_markup=reply_markup
        )
    if  update.callback_query:
        await update.callback_query.message.reply_text(
            message + "\n\n<b>Each 3 shortlists cost 5 tokens.</b>\n\nHow many shortlists would you like to buy? Please enter a multiple of 3.", parse_mode='HTML', reply_markup=reply_markup
        )
    return CHOOSE_AMOUNT

# Function to handle the amount choice and move to confirmation
async def handle_amount_choice(update: Update, context: CallbackContext) -> int:
    logger.info("Entered handle_amount_choice function")
    chat_id = update.effective_chat.id

    # Retrieve the number of shortlists the user wants to buy
    try:
        num_shortlists = int(update.message.text)
    except ValueError:
        await update.message.reply_text("Please enter a valid number.")
        return CHOOSE_AMOUNT

    if num_shortlists <= 0 or num_shortlists % 3 != 0:
        await update.message.reply_text("Please enter a valid number that is a multiple of 3.")
        return CHOOSE_AMOUNT

    tokens_required = (num_shortlists // 3) * 5

    # Retrieve current tokens balance
    query_tokens = "SELECT tokens FROM token_balance WHERE chat_id = :chat_id"
    tokens_result = await safe_get_db(query_tokens, {"chat_id": chat_id})
    tokens = tokens_result[0][0] if tokens_result else 0

    if tokens < tokens_required:
        await update.message.reply_text(
            "Insufficient tokens. Please purchase more tokens at /purchase_tokens."
        )
        return ConversationHandler.END

    # Store purchase details in user_data
    context.user_data['num_shortlists'] = num_shortlists
    context.user_data['tokens_required'] = tokens_required

    # Create a confirmation button
    keyboard = [
        [InlineKeyboardButton("Confirm Purchase", callback_data="confirm_purchase")],
        [InlineKeyboardButton("Cancel", callback_data="shortlist_cancel_purchase")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Send the confirmation message
    await update.message.reply_text(
        f"You are about to purchase {num_shortlists} shortlists for {tokens_required} tokens.\n\n"
        f"Do you want to proceed?",
        reply_markup=reply_markup
    )

    return CONFIRM_PURCHASE

# Function to handle the purchase confirmation
async def confirm_purchase(update: Update, context: CallbackContext) -> int:
    logger.info("Entered confirm_purchase function")
    chat_id = update.effective_chat.id

    # Retrieve the number of shortlists and tokens required from user_data
    num_shortlists = context.user_data.get('num_shortlists')
    tokens_required = context.user_data.get('tokens_required')

    if not num_shortlists or not tokens_required:
        await update.callback_query.message.edit_text("Something went wrong. Please try again.")
        return ConversationHandler.END

    # Update the token_balance and shortlist_balance tables
    update_tokens_query = "UPDATE token_balance SET tokens = tokens - :tokens_required WHERE chat_id = :chat_id"
    await safe_set_db(update_tokens_query, {"tokens_required": tokens_required, "chat_id": chat_id})

    # If have a chat_id entry in the shortlist_balance table, update value
    if context.user_data['entry_present']:
        update_shortlists_query = "UPDATE shortlist_balance SET shortlist = shortlist + :new_shortlists WHERE chat_id = :chat_id"
        await safe_set_db(update_shortlists_query, {"chat_id": chat_id, "new_shortlists": num_shortlists})
    #Else, create new entry in the shortlist_balance table
    else:
        insert_shortlists_query = "INSERT INTO shortlist_balance (chat_id, shortlist) VALUES (:chat_id, :new_shortlists)"
        await safe_set_db(insert_shortlists_query, {"chat_id": chat_id, "new_shortlists": num_shortlists})


    # Retrieve updated balances
    updated_tokens_result = await safe_get_db("SELECT tokens FROM token_balance WHERE chat_id = :chat_id", {"chat_id": chat_id})
    updated_shortlists_result = await safe_get_db("SELECT shortlist FROM shortlist_balance WHERE chat_id = :chat_id", {"chat_id": chat_id})

    updated_tokens = updated_tokens_result[0][0] if updated_tokens_result else 0
    updated_shortlists = updated_shortlists_result[0][0] if updated_shortlists_result else 0

    # Send confirmation message with updated balances
    await update.callback_query.message.edit_text(
        f"Purchase successful!\n\n"
        f"You now have a total of <b>{updated_shortlists} shortlists</b>.\n"
        f"You have <b>{updated_tokens} tokens</b> remaining.",
        parse_mode='HTML'
    )

    return ConversationHandler.END

# Function to handle cancellation
async def shortlist_cancel(update: Update, context: CallbackContext) -> int:
    logger.info("Entered cancel function")
    callback_query = update.callback_query
    logger.info(f"CALLBACK QUERY: {callback_query}")
    await callback_query.answer()
    await callback_query.message.edit_text("Shortlist purchasing canceled.")
    return ConversationHandler.END

###########################################################################################################################################################   
# Shortlisting function
SELECT_JOB, SHOW_APPLICANTS, DONE, PURCHASE_SHORTLISTS, CHOOSE_AMOUNT, CONFIRM_PURCHASE  = range(6)

# Function to start the shortlisting process
async def shortlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Entered shortlist function")
    chat_id = update.effective_chat.id
    context.user_data['chat_id'] = chat_id

    # Retrieve agency_id(s) for this chat_id from the agencies table
    query = "SELECT id FROM agencies WHERE chat_id = :chat_id"
    agency_ids = await safe_get_db(query, {"chat_id": chat_id})

    if not agency_ids:
        await update.message.reply_text("You do not have an agency profile! Type /register to create one.")
        return ConversationHandler.END

    agency_ids = [row[0] for row in agency_ids]

    # Retrieve job titles and industries for the agency_id(s) from the job_posts table
    query = "SELECT id, job_title, company_name FROM job_posts WHERE agency_id IN :agency_ids AND status = 'approved'"
    job_posts = await safe_get_db(query, {"agency_ids": tuple(agency_ids)})

    if not job_posts:
        await update.message.reply_text("No job posts found for your agencies. To post a job, type /jobpost")
        return ConversationHandler.END

    # Retrieve shortlist balance
    query_shortlists = "SELECT shortlist FROM shortlist_balance WHERE chat_id = :chat_id"
    shortlists_result = await safe_get_db(query_shortlists, {"chat_id": chat_id})
    shortlists = shortlists_result[0][0] if shortlists_result else 0
    context.user_data['shortlists'] = shortlists

    # Check if shortlists are zero
    if shortlists <= 0:
        keyboard = [
            [InlineKeyboardButton("Proceed to view", callback_data="proceed")],
            [InlineKeyboardButton("Cancel", callback_data="cancel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "You have no shortlists available.\n\nYou can only view the applicants details but not shortlist them.\n\n"
            "Please purchase more at /purchase_shortlists if you need to shortlist applicants.",
            reply_markup=reply_markup
        )
        return SELECT_JOB

    # Display available jobs
    keyboard = [[InlineKeyboardButton(f"{job[0]} - {job[1]} - {job[2]}", callback_data=str(job[0]))] for job in job_posts]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Select a job to shortlist applicants for:", reply_markup=reply_markup)
    return SELECT_JOB

# Function to handle job selection
async def select_job(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Entered select job function")
    callback_query = update.callback_query
    await callback_query.answer()
    ##############################
    # Handle the "Proceed" and "Cancel" actions
    if callback_query.data == "proceed":
        chat_id = context.user_data['chat_id']

        # Retrieve agency_id(s) for this chat_id from the agencies table
        query = "SELECT id FROM agencies WHERE chat_id = :chat_id"
        agency_ids = await safe_get_db(query, {"chat_id": chat_id})

        if not agency_ids:
            await callback_query.message.reply_text("You do not have an agency profile! Type /register to create one.")
            return ConversationHandler.END

        agency_ids = [row[0] for row in agency_ids]

        # Retrieve job titles and industries for the agency_id(s) from the job_posts table
        query = "SELECT id, job_title, company_name FROM job_posts WHERE agency_id IN :agency_ids AND status = 'approved'"
        job_posts = await safe_get_db(query, {"agency_ids": tuple(agency_ids)})

        if not job_posts:
            await callback_query.message.reply_text("No job posts found for your agencies. To post a job, type /jobpost")
            return ConversationHandler.END

        # Display available jobs
        keyboard = [[InlineKeyboardButton(f"{job[1]} - {job[2]}", callback_data=str(job[0]))] for job in job_posts]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await callback_query.message.reply_text("Select a job to view applicants for:", reply_markup=reply_markup)
        return SELECT_JOB

    elif callback_query.data == "cancel":
        await callback_query.message.edit_text("Shortlisting process cancelled.")
        return ConversationHandler.END

    ##############################
    job_id = int(callback_query.data)
    context.user_data['selected_job_id'] = job_id

    # Retrieve the selected job title and company industry
    query = "SELECT job_title, company_name FROM job_posts WHERE id = :job_id"
    job = await safe_get_db(query, {"job_id": job_id})

    if not job:
        await callback_query.message.reply_text("Selected job not found. Please try again later.")
        return ConversationHandler.END

    job_title, company_industry = job[0]

    # Update the original message to indicate the selected job
    await callback_query.message.edit_text(
        f"You have picked <b>{job_title}</b> - <b>{company_industry}</b>",
        parse_mode='HTML'
    )

    # Retrieve applicant IDs for the selected job from the job_applications table
    query = "SELECT applicant_id FROM job_applications WHERE job_id = :job_id AND shortlist_status = 'no'"
    applicant_ids = await safe_get_db(query, {"job_id": job_id})

    if not applicant_ids:
        await callback_query.message.reply_text("No applicants found for the selected job. Please try again later.")
        return ConversationHandler.END

    applicant_ids = [row[0] for row in applicant_ids]
    context.user_data['remaining_applicants'] = applicant_ids # keep track to end convohandler once all applicants are shortlisted

    # Retrieve applicant details for each applicant_id
    for i, applicant_id in enumerate(applicant_ids, start=1):
        query = (
            "SELECT dob, past_exp, citizenship, race, gender, education,lang_spoken "
            "FROM applicants WHERE id = :applicant_id"
        )
        applicant = await safe_get_db(query, {"applicant_id": applicant_id})
        if applicant:
            dob, past_exp, citizenship, race, gender, education, lang_spoken = applicant[0]
            applicant_details = (
                f"<b>Applicant {i}</b>\n\n"
                f"<b>DOB:</b> {dob}\n"
                f"<b>Languages Spoken:</b> {lang_spoken}\n"
                f"<b>Past Experiences:</b> {past_exp}\n"
                f"<b>Citizenship:</b> {citizenship}\n"
                f"<b>Race:</b> {race}\n"
                f"<b>Gender:</b> {gender}\n"
                f"<b>Education:</b> {education}"
            )
            keyboard = [
                [InlineKeyboardButton("Shortlist", callback_data=f"shortlist|{applicant_id}")],
                [InlineKeyboardButton("Done", callback_data="done")]
            ]

            # Build keyboard based on shortlist availability. If 0 shortlists, no shortlist button shld be displayed
            # Initialize reply_markup with default empty state
            keyboard = []
            reply_markup = InlineKeyboardMarkup(keyboard)
            shortlists = context.user_data.get('shortlists',0)
            # Add Shortlist button if shortlists are available
            
            # Add Shortlist button if shortlists are available
            if shortlists > 0:
                keyboard.append([InlineKeyboardButton("Shortlist", callback_data=f"shortlist|{applicant_id}")])
            else:
                keyboard.append([InlineKeyboardButton("Shortlist", callback_data="no_shortlists")])

            # Add Done button
            keyboard.append([InlineKeyboardButton("Done", callback_data="done")])

            # Update reply_markup only if keyboard is not empty
            if keyboard:
                reply_markup = InlineKeyboardMarkup(keyboard)

            await callback_query.message.reply_text(applicant_details, reply_markup=reply_markup, parse_mode='HTML')

    return SHOW_APPLICANTS

# Function to handle no_shortlists callback and redirect to purchase_shortlists_handler
async def handle_no_shortlists(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("No shortlists available, redirecting to purchase_shortlists")
    await update.callback_query.answer()  # Acknowledge the callback query
    await update.callback_query.message.reply_text(
        "You have no more shortlists available! Please purchase shortlists to continue."
    )
    
    logger.info("Entered purchase_shortlists function")
    chat_id = update.effective_chat.id

    # Retrieve tokens and shortlists balance from the database
    query_tokens = "SELECT tokens FROM token_balance WHERE chat_id = :chat_id"
    tokens_result = await safe_get_db(query_tokens, {"chat_id": chat_id})
    tokens = tokens_result[0][0] if tokens_result else 0

    query_shortlists = "SELECT shortlist FROM shortlist_balance WHERE chat_id = :chat_id"
    shortlists_result = await safe_get_db(query_shortlists, {"chat_id": chat_id})
    
    # Check if entry for chat_id in shortlist_balance table
    context.user_data['entry_present'] = 1 if shortlists_result else 0

    # Set shortlist number to 0 if no entry present in shortlist_balance table
    shortlists = shortlists_result[0][0] if shortlists_result else 0

    # Display current balances to the user
    message = f"You currently have:\n{tokens} tokens\n{shortlists} shortlists"

    if tokens < 5:
        if update.message:
            await update.message.reply_text(
                message + "\n\n<b>Each 3 shortlists cost 5 tokens.</b>\n\nYou have insufficient tokens. Purchase more tokens at /purchase_tokens.", parse_mode='HTML'
            )
        if update.callback_query:
            await update.callback_query.message.reply_text(
                message + "\n\n<b>Each 3 shortlists cost 5 tokens.</b>\n\nYou have insufficient tokens. Purchase more tokens at /purchase_tokens.", parse_mode='HTML'
            )
        return ConversationHandler.END

    # Ask user how many shortlists they want to buy
    keyboard = [
        [InlineKeyboardButton("Cancel", callback_data="shortlist_cancel_purchase")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        await update.message.reply_text(
            message + "\n\n<b>Each 3 shortlists cost 5 tokens.</b>\n\nHow many shortlists would you like to buy? Please enter a multiple of 3.", parse_mode='HTML', reply_markup=reply_markup
        )
    if  update.callback_query:
        await update.callback_query.message.reply_text(
            message + "\n\n<b>Each 3 shortlists cost 5 tokens.</b>\n\nHow many shortlists would you like to buy? Please enter a multiple of 3.", parse_mode='HTML', reply_markup=reply_markup
        )
    return CHOOSE_AMOUNT

# Function to handle the amount choice and move to confirmation
async def handle_amount_choice(update: Update, context: CallbackContext) -> int:
    logger.info("Entered handle_amount_choice function")
    chat_id = update.effective_chat.id

    # Retrieve the number of shortlists the user wants to buy
    try:
        num_shortlists = int(update.message.text)
    except ValueError:
        await update.message.reply_text("Please enter a valid number.")
        return CHOOSE_AMOUNT

    if num_shortlists <= 0 or num_shortlists % 3 != 0:
        await update.message.reply_text("Please enter a valid number that is a multiple of 3.")
        return CHOOSE_AMOUNT

    tokens_required = (num_shortlists // 3) * 5

    # Retrieve current tokens balance
    query_tokens = "SELECT tokens FROM token_balance WHERE chat_id = :chat_id"
    tokens_result = await safe_get_db(query_tokens, {"chat_id": chat_id})
    tokens = tokens_result[0][0] if tokens_result else 0

    if tokens < tokens_required:
        await update.message.reply_text(
            "Insufficient tokens. Please purchase more tokens at /purchase_tokens."
        )
        return ConversationHandler.END

    # Store purchase details in user_data
    context.user_data['num_shortlists'] = num_shortlists
    context.user_data['tokens_required'] = tokens_required

    # Create a confirmation button
    keyboard = [
        [InlineKeyboardButton("Confirm Purchase", callback_data="confirm_purchase")],
        [InlineKeyboardButton("Cancel", callback_data="shortlist_cancel_purchase")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Send the confirmation message
    await update.message.reply_text(
        f"You are about to purchase {num_shortlists} shortlists for {tokens_required} tokens.\n\n"
        f"Do you want to proceed?",
        reply_markup=reply_markup
    )

    return CONFIRM_PURCHASE

# Function to handle the purchase confirmation
async def confirm_purchase(update: Update, context: CallbackContext) -> int:
    logger.info("Entered confirm_purchase function")
    chat_id = update.effective_chat.id

    # Retrieve the number of shortlists and tokens required from user_data
    num_shortlists = context.user_data.get('num_shortlists')
    tokens_required = context.user_data.get('tokens_required')

    if not num_shortlists or not tokens_required:
        await update.callback_query.message.edit_text("Something went wrong. Please try again.")
        return ConversationHandler.END

    # Update the token_balance and shortlist_balance tables
    update_tokens_query = "UPDATE token_balance SET tokens = tokens - :tokens_required WHERE chat_id = :chat_id"
    await safe_set_db(update_tokens_query, {"tokens_required": tokens_required, "chat_id": chat_id})

    # If have a chat_id entry in the shortlist_balance table, update value
    if context.user_data['entry_present']:
        update_shortlists_query = "UPDATE shortlist_balance SET shortlist = shortlist + :new_shortlists WHERE chat_id = :chat_id"
        await safe_set_db(update_shortlists_query, {"chat_id": chat_id, "new_shortlists": num_shortlists})
    #Else, create new entry in the shortlist_balance table
    else:
        insert_shortlists_query = "INSERT INTO shortlist_balance (chat_id, shortlist) VALUES (:chat_id, :new_shortlists)"
        await safe_set_db(insert_shortlists_query, {"chat_id": chat_id, "new_shortlists": num_shortlists})


    # Retrieve updated balances
    updated_tokens_result = await safe_get_db("SELECT tokens FROM token_balance WHERE chat_id = :chat_id", {"chat_id": chat_id})
    updated_shortlists_result = await safe_get_db("SELECT shortlist FROM shortlist_balance WHERE chat_id = :chat_id", {"chat_id": chat_id})

    updated_tokens = updated_tokens_result[0][0] if updated_tokens_result else 0
    updated_shortlists = updated_shortlists_result[0][0] if updated_shortlists_result else 0

    # Send confirmation message with updated balances
    await update.callback_query.message.edit_text(
        f"Purchase successful!\n\n"
        f"You now have a total of <b>{updated_shortlists} shortlists</b>.\n"
        f"You have <b>{updated_tokens} tokens</b> remaining.",
        parse_mode='HTML'
    )

    return ConversationHandler.END

# Function to handle cancellation
async def shortlist_cancel(update: Update, context: CallbackContext) -> int:
    logger.info("Entered cancel function")
    callback_query = update.callback_query
    logger.info(f"CALLBACK QUERY: {callback_query}")
    await callback_query.answer()
    await callback_query.message.edit_text("Shortlist purchasing canceled.")
    return ConversationHandler.END


# Function to handle applicant shortlisting
async def shortlist_applicant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Entered shortlist_applicant function")
    
    callback_query = update.callback_query
    await callback_query.answer()  # Acknowledge the callback query

    # Extract applicant_id from callback data
    _, applicant_id = callback_query.data.split('|')
    job_id = context.user_data.get('selected_job_id')
    chat_id = context.user_data.get('chat_id')  # Ensure chat_id is available
    logger.info(f"CHAT ID: {chat_id}")

    if not job_id:
        await callback_query.message.reply_text("No job selected. Please select a job first.")
        return SHOW_APPLICANTS  # Continue in the SHOW_APPLICANTS state

    # Update the shortlist status for the selected applicant and job
    logger.info("Updating database")
    query = "UPDATE job_applications SET shortlist_status = 'yes' WHERE job_id = :job_id AND applicant_id = :applicant_id"
    await safe_set_db(query, {"job_id": job_id, "applicant_id": applicant_id})

    # Update the shortlist_balance table
    logger.info("Updating shortlist_balance table")
    query = """
        INSERT INTO shortlist_balance (chat_id, shortlist)
        VALUES (:chat_id, :shortlist)
        ON DUPLICATE KEY UPDATE shortlist = shortlist - 1
    """
    await safe_set_db(query, {"chat_id": chat_id, "shortlist": context.user_data.get('shortlists', 0)})

    # Remove the applicant from the list of remaining applicants
    remaining_applicants = context.user_data.get('remaining_applicants', [])
    remaining_applicants.remove(applicant_id)
    context.user_data['remaining_applicants'] = remaining_applicants
    
    # Retrieve the remaining shortlists
    context.user_data['shortlists'] -= 1
    remaining_shortlists = context.user_data['shortlists']

    # If no more remaining shortlists, end convohandler
    if remaining_shortlists <= 0:
        await callback_query.message.edit_text(
            "You have no more shortlists available. Please purchase more at /purchase_shortlists."
        )
        return ConversationHandler.END

    # Check if there are any remaining applicants to shortlist
    if remaining_applicants:
        # Provide feedback that the applicant has been shortlisted successfully
        # remaining_shortlists = context.user_data['shortlists']
        await callback_query.message.edit_text(
            f"Applicant has been shortlisted successfully!\n\n"
            f"You have {remaining_shortlists} shortlists left."
        )
        return SHOW_APPLICANTS  # Continue in the SHOW_APPLICANTS state
    # If no more applicants, end the conversation
    else:
        await callback_query.message.edit_text(
            f"All applicants have been shortlisted.\n\nYou have {remaining_shortlists} shortlists left. "
        )
        return ConversationHandler.END

# Function to handle "done" button click
async def done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Entered done function")
    callback_query = update.callback_query
    logger.info(f"CALLBACK QUERY: {callback_query}")
    await callback_query.answer()

    # Retrieve the remaining shortlists
    shortlists = context.user_data.get('shortlists', 0)
    
    await callback_query.message.reply_text(
        f"You have completed the shortlisting process.\n\n"
        f"You have a total of <b>{shortlists} shortlists</b> remaining.\n\n"
        "If you need more shortlists, please purchase more at /purchase_shortlists.",
        parse_mode='HTML'
    )
    
    # Optionally, clean up any data or state if needed
    context.user_data.clear()  # Clear user_data if necessary
    
    return ConversationHandler.END

###########################################################################################################################################################
# View Shortlisted applicants

VIEW_JOBS, VIEW_APPLICANTS = range(2)
#TODO add job id to when u view job to shortlist for
# Function to handle /view_shortlisted command
async def view_shortlisted(update: Update, context: CallbackContext) -> int:
    chat_id = update.effective_chat.id
    context.user_data['chat_id'] = chat_id
    logger.info("view_shortlisted() called")
    # Retrieve agency IDs for the given chat_id
    query = "SELECT id FROM agencies WHERE chat_id = :chat_id"
    agency_ids = await safe_get_db(query, {"chat_id": chat_id})

    if not agency_ids:
        await update.message.reply_text("No agencies found for your chat ID.")
        return ConversationHandler.END
    
    agency_ids = [row[0] for row in agency_ids]

    # Retrieve job titles and industries for the agency_id(s) from the job_posts table
    query = "SELECT id, job_title, company_name FROM job_posts WHERE agency_id IN :agency_ids AND status = 'approved'"
    jobs_result = await safe_get_db(query, {"agency_ids": tuple(agency_ids)})

    if not jobs_result:
        await update.message.reply_text("No jobs found for your agencies.")
        return ConversationHandler.END

    # Create buttons for each job
    keyboard = [[InlineKeyboardButton(f"{job_title} - {company_industry}", callback_data=f"view_applicants_{job_id}")]
                for job_id, job_title, company_industry in jobs_result]
    keyboard.append([InlineKeyboardButton("Cancel", callback_data="cancel_view_shortlisted")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    view_shortlist_message = await update.message.reply_text("Select a job to view shortlisted applicants:", reply_markup=reply_markup)
    context.user_data['view_shortlist_message_id'] = view_shortlist_message.id
    return VIEW_JOBS

# Function to handle job selection and show applicants
async def view_applicants(update: Update, context: CallbackContext) -> int:
    callback_query = update.callback_query
    await callback_query.answer()

    job_id = callback_query.data.split('_')[2]  # Extract job_id from callback data
    job_id = int(job_id)
    context.user_data['selected_job_id'] = job_id

    # Retrieve the selected job title and company industry
    query = "SELECT job_title, company_name FROM job_posts WHERE id = :job_id"
    job = await safe_get_db(query, {"job_id": job_id})

    if not job:
        await callback_query.message.reply_text("Selected job not found. Please try again later.")
        return ConversationHandler.END

    job_title, company_industry = job[0]

    # Update the original message to indicate the selected job
    await callback_query.message.edit_text(
        f"You have picked <b>{job_title}</b> - <b>{company_industry}</b>",
        parse_mode='HTML'
    )

    # Retrieve applicant IDs for the selected job
    query_applicants = "SELECT applicant_id FROM job_applications WHERE job_id = :job_id AND shortlist_status = 'yes'"
    applicants_result = await safe_get_db(query_applicants, {"job_id": job_id})
    applicant_ids = [row[0] for row in applicants_result]

    if not applicant_ids:
        await callback_query.message.edit_text("You have not shortlisted any applicants.\n You can shortlist candidates at /shortlist.")
        return ConversationHandler.END

    # Send applicant details
    for i, applicant_id in enumerate(applicant_ids, start=1):
        query_details = (
            "SELECT user_handle, name, dob, past_exp, citizenship, race, gender, education, lang_spoken, whatsapp_no "
            "FROM applicants WHERE id = :applicant_id"
        )
        details_result = await safe_get_db(query_details, {"applicant_id": applicant_id})
        if details_result:
            user_handle, name, dob, past_exp, citizenship, race, gender, education, lang_spoken, wa_number = details_result[0]
            await callback_query.message.reply_text(
                f"<b>Applicant {i}</b>\n\n"
                f"<b>User Handle:</b> @{user_handle}\n"
                f"<b>Name:</b> {name}\n"
                f"<b>DOB:</b> {dob}\n"
                f"<b>Languages Spoken:</b> {lang_spoken}\n"
                f"<b>Past Experience:</b> {past_exp}\n"
                f"<b>Citizenship:</b> {citizenship}\n"
                f"<b>Race:</b> {race}\n"
                f"<b>Gender:</b> {gender}\n"
                f"<b>Education:</b>{education}\n"
                f"<b>WA Number:</b> {wa_number}", parse_mode='HTML'
            )

    # Add a cancel button
    await callback_query.message.reply_text("End of the list. If you want to cancel, press the button below.")
    keyboard = [[InlineKeyboardButton("Cancel", callback_data="cancel_view_shortlisted")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await callback_query.message.reply_text("If you want to cancel, press the button below.", reply_markup=reply_markup)

    return VIEW_JOBS

# Function to handle cancellation
async def cancel_view_shortlisted(update: Update, context: CallbackContext) -> int:
    logger.info("Entered cancel_view_shortlisted")
    callback_query = update.callback_query
    logger.info(f"cancel_view_shortlisted query: {callback_query}")
    await callback_query.answer()
    await callback_query.message.edit_text("Viewing shortlisted applicants has been canceled.")
    return ConversationHandler.END




# ###########################################################################################################################################################   
# # Cancel
# async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
#     """Cancels and ends the conversation."""
#     user = update.message.from_user
#     logger.info("User %s canceled the conversation.", user.first_name)
#     await update.message.reply_text(
#         "Bye! I hope we can talk again some day.", reply_markup=ReplyKeyboardRemove()
#     )

#     return ConversationHandler.END

###########################################################################################################################################################   
# View Profile
async def view_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id == ADMIN_CHAT_ID: context.user_data['is_admin'] = True
    else: context.user_data['is_admin']= False
    # user_handle = update.effective_user.username
    logger.info(chat_id)
    if context.user_data['is_admin']:
        # Select all profiles if admin
        agencies_query_string = "SELECT id, agency_name, user_handle FROM agencies"
        applicants_query_string = "SELECT id, name, user_handle FROM applicants"
        params = {}
    else:
        agencies_query_string = "SELECT id,agency_name, user_handle FROM agencies WHERE chat_id = :chat_id"
        applicants_query_string = "SELECT id,name, user_handle FROM applicants WHERE chat_id = :chat_id"
        params = {"chat_id": chat_id}

    results = await safe_get_db(agencies_query_string, params)
    agency_profiles = results
    results = await safe_get_db(applicants_query_string, params)
    applicant_profiles = results

    keyboard = []

    for uuid, agency_name, user_handle in agency_profiles:
        keyboard.append([InlineKeyboardButton(f"{user_handle}'s agency - {agency_name}", callback_data=f"view_agency_{uuid}")])
    for uuid, applicant_name, user_handle in applicant_profiles:
        keyboard.append([InlineKeyboardButton(f"{user_handle}'s applicant - {applicant_name}", callback_data=f"view_applicant_{uuid}")])
        # Check if both profiles are empty
    if not agency_profiles and not applicant_profiles:
        await update.message.reply_text('You have no profiles to delete.')
        return
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Select the profile you want to view:", reply_markup=reply_markup)
    logger.info("exit view profile function")

async def view_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("View button clicked!")
    query = update.callback_query
    query_data = query.data
    profile_type, uuid = query_data.split('_')[1:]
    logger.info(query_data)


    # View selected applicant profile
    if profile_type == "applicant":
        query_string = "SELECT id, user_handle, name, dob, past_exp, citizenship, race, gender, education, whatsapp_no, chat_id FROM applicants WHERE id = :id"
        params = {"id": uuid}
        results = await safe_get_db(query_string, params)
        selected_applicant = results[0]
        applicant_uuid, applicant_user_handle, applicant_name, applicant_dob, applicant_past_exp, applicant_citizenship, applicant_race, applicant_gender, applicant_education, applicant_whatsapp_no, applicant_chat_id = selected_applicant
        await query.answer()
        # Display details to admin
        await query.edit_message_text(f'''
UUID: {applicant_uuid}\n
Telegram Handle: {applicant_user_handle}\n
Name: {applicant_name}\n
Date of Birth: {applicant_dob}\n
Past Experiences: {applicant_past_exp}\n
Citizenship: {applicant_citizenship}\n
Race: {applicant_race}\n
Gender: {applicant_gender}\n
Education: {applicant_education}\n
Whatsapp No: {applicant_whatsapp_no}\n
Chat ID: {applicant_chat_id}\n
        ''')

    # View selected agency profile
    elif profile_type == "agency":
        query_string = "SELECT id, user_handle, name, agency_name, agency_uen, chat_id FROM agencies WHERE id = :id"
        params = {"id": uuid}
        results = await safe_get_db(query_string, params)
        selected_agency = results[0]
        agency_uuid, agency_user_handle, agency_name, agency_agency_name, agency_uen, agency_chat_id = selected_agency
        await query.answer()
        # Display details to admin
        await query.edit_message_text(f'''
UUID: {agency_uuid}\n
Telegram Handle: {agency_user_handle}\n
Name: {agency_name}\n
Agency Name: {agency_agency_name}\n
Agency UEN: {agency_uen}\n
Chat ID: {agency_chat_id}\n
        ''')

# Delete profile
async def delete_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if chat_id == ADMIN_CHAT_ID: context.user_data['is_admin'] = True
    else: context.user_data['is_admin']= False
    # user_handle = update.effective_user.username
    logger.info(chat_id)
    if context.user_data['is_admin']:
        # Select all profiles if admin
        agencies_query_string = "SELECT id, agency_name FROM agencies"
        applicants_query_string = "SELECT id, name FROM applicants"
        params = {}
    else:
        agencies_query_string = "SELECT id,agency_name FROM agencies WHERE chat_id = :chat_id"
        applicants_query_string = "SELECT id,name FROM applicants WHERE chat_id = :chat_id"
        params = {"chat_id": chat_id}

    results = await safe_get_db(agencies_query_string, params)
    agency_profiles = results
    results = await safe_get_db(applicants_query_string, params)
    applicant_profiles = results

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
# Add subscription packages

GET_SUB_NAME, GET_NUM_MONTHS, GET_TOKENS_PER_MONTH, GET_PRICE = range(4)

async def start_add_subscription(update: Update, context: CallbackContext) -> int:
    if (update.effective_chat.id != ADMIN_CHAT_ID):
        return ConversationHandler.END
    await update.message.reply_text("Please enter the subscription name:")
    return GET_SUB_NAME

async def get_subscription_name(update: Update, context: CallbackContext) -> int:
    context.user_data['sub_name'] = update.message.text
    await update.message.reply_text("Please enter the number of months for the subscription:")
    return GET_NUM_MONTHS

async def get_num_months(update: Update, context: CallbackContext) -> int:
    try:
        num_months = int(update.message.text)
        context.user_data['num_months'] = num_months
        await update.message.reply_text("Please enter the number of tokens per month:")
        return GET_TOKENS_PER_MONTH
    except ValueError:
        await update.message.reply_text("Invalid input. Please enter a valid number of months.")
        return GET_NUM_MONTHS

async def get_tokens_per_month(update: Update, context: CallbackContext) -> int:
    try:
        tokens_per_month = int(update.message.text)
        context.user_data['tokens_per_month'] = tokens_per_month
        await update.message.reply_text("Please enter the total price of the subscription package:")
        return GET_PRICE
    except ValueError:
        await update.message.reply_text("Invalid input. Please enter a valid number of tokens per month.")
        return GET_TOKENS_PER_MONTH

async def get_price(update: Update, context: CallbackContext) -> int:
    try:
        price = float(update.message.text)
        context.user_data['price'] = price
        
        query_string = "INSERT INTO subscription_packages (sub_name, number_of_tokens, duration_months, price) VALUES (:sub_name, :number_of_tokens, :duration_months, :price)"
        params = {
            "sub_name": context.user_data['sub_name'],
            "number_of_tokens": context.user_data['tokens_per_month'],
            "duration_months": context.user_data['num_months'],
            "price": context.user_data['price']
        }
        await safe_set_db(query_string, params)
        await update.message.reply_text("Subscription added successfully!")
        context.user_data.clear()
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("Invalid price. Please enter a valid number/decimal for the price.")
        return GET_PRICE

async def cancel(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text("Subscription addition has been cancelled.")
    context.user_data.clear()
    return ConversationHandler.END
###########################################################################################################################################################   

# Add token packages
PACKAGE_NAME, NUMBER_OF_TOKENS, PRICE, DESCRIPTION, VALIDITY = range(5)

# Function to handle /addpackage command
async def add_package(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if (update.effective_chat.id != ADMIN_CHAT_ID):
        return ConversationHandler.END    
    await update.message.reply_text("Please enter the package name:")
    return PACKAGE_NAME

# Function to handle package name input
async def package_name_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    package_name = update.message.text
    context.user_data['package_name'] = package_name
    await update.message.reply_text("Please enter the number of tokens for this package:")
    return NUMBER_OF_TOKENS

# Function to handle number of tokens input
async def number_of_tokens_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        number_of_tokens = int(update.message.text)
        context.user_data['number_of_tokens'] = number_of_tokens
        await update.message.reply_text("Please enter the price of this package:")
        return PRICE
    except ValueError:
        await update.message.reply_text("Invalid input. Please enter an integer for the number of tokens:")
        return NUMBER_OF_TOKENS

# Function to handle the price input
async def purchase_amount_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        purchase_amount = float(update.message.text)
        context.user_data['price'] = purchase_amount
        await update.message.reply_text("Please enter the description to be displayed for the package:")
        return DESCRIPTION
    except ValueError:
        await update.message.reply_text("Invalid input. Please enter a decimal number for the purchase amount:")
        return PRICE

# Function to handle the description input
async def description_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    description = update.message.text
    context.user_data['description'] = description
    await update.message.reply_text("Please enter the validity period for this package (in days):")
    return VALIDITY

# Function to handle the validity input and store the package in the database
async def validity_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        validity = int(update.message.text)
        context.user_data['validity'] = validity
        query_string = """
            INSERT INTO token_packages (package_name, number_of_tokens, price, description, validity)
            VALUES (:package_name, :number_of_tokens, :price, :description, :validity)
        """
        params = {
            'package_name': context.user_data['package_name'],
            'number_of_tokens': context.user_data['number_of_tokens'],
            'price': context.user_data['price'],
            'description': context.user_data['description'],
            'validity': context.user_data['validity']
        }
        await safe_set_db(query_string, params)

        await update.message.reply_text("Token package added successfully!")
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("Invalid input. Please enter an integer for the validity period:")
        return VALIDITY
###########################################################################################################################################################
# Delete subscription package
SELECT_SUBSCRIPTION, DELETE_CONFIRMATION, DELETE_SUBSCRIPTION = range(3)

async def list_subscriptions(update: Update, context: CallbackContext) -> int:
    # Retrieve subscription packages
    if (update.effective_chat.id != ADMIN_CHAT_ID):
        return ConversationHandler.END
    query = "SELECT id, sub_name, number_of_tokens, duration_months, price FROM subscription_packages"
    subscription_packages = await safe_get_db(query, {})

    if not subscription_packages:
        await update.message.reply_text('No subscription packages available.')
        return ConversationHandler.END

    # Format the list of subscription packages
    text = "Please choose the package you would like to delete.\n\nAvailable Subscription Packages:\n\n"
    keyboard = []

    for sub_id, sub_name, tokens, duration, price in subscription_packages:
        text += f"<b>Subscription name:</b> {sub_name}\n<b>Tokens per month:</b> {tokens}\n<b>Duration:</b> {duration} months\n<b>Total price:</b> ${price}\n\n"
        keyboard.append([InlineKeyboardButton(sub_name, callback_data=f"delete_sub|{sub_id}")])

    # keyboard.append([InlineKeyboardButton("Cancel", callback_data="cancel_delete")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')

    return DELETE_CONFIRMATION

async def confirm_deletion(update: Update, context: CallbackContext) -> int:
    logger.info("Entered confrim_deletion")
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("delete_sub|"):
        sub_id = data.split('|')[1]

        # Confirm deletion
        keyboard = [
            [InlineKeyboardButton("Confirm", callback_data=f"confirm_delete|{sub_id}")],
            [InlineKeyboardButton("Cancel", callback_data="cancel_delete")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text("Are you sure you want to delete this subscription package?", reply_markup=reply_markup)
        return DELETE_SUBSCRIPTION

async def delete_subscription(update: Update, context: CallbackContext) -> int:
    logger.info("Entered delete_subscription")
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("confirm_delete|"):
        logger.info("Enter confirm deleted elif statement")
        sub_id = data.split('|')[1]

        # Delete the subscription package
        query_string = "DELETE FROM subscription_packages WHERE id = :sub_id"
        params = {"sub_id": sub_id}
        await safe_set_db(query_string, params)

        await query.edit_message_text("Subscription package deleted successfully!")

    elif data == "cancel_delete":
        logger.info("Enter cancel_delete elif statement")
        await query.edit_message_text("Deletion process canceled.")

    return ConversationHandler.END


###########################################################################################################################################################   
#Delete token package

SELECT_PACKAGE, CONFIRM_DELETE = range(2)

# Function to handle /deletepackage command
async def delete_package(update: Update, context: CallbackContext) -> int:
    if (update.effective_chat.id != ADMIN_CHAT_ID):
        return ConversationHandler.END    
    try:
        async with AsyncSessionLocal() as conn:
            results = await conn.execute(
                sqlalchemy.text(
                    "SELECT package_id, package_name, description FROM token_packages"
                )
            )
            packages = results.fetchall()

        if not packages:
            await update.message.reply_text("No packages available to delete.")
            return ConversationHandler.END

        # Prepare the message with package descriptions
        package_info = "\n\n".join([f"{pkg[1]}: {pkg[2]}" for pkg in packages])
        keyboard = [[InlineKeyboardButton(pkg[1], callback_data=str(pkg[0]))] for pkg in packages]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(f"Select a package to delete:\n\n{package_info}", reply_markup=reply_markup)
        return SELECT_PACKAGE
    except Exception as e:
        await update.message.reply_text(f"An error occurred: {e}")
        return ConversationHandler.END

# Function to handle package selection
async def select_package(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    context.user_data['package_id'] = int(query.data)

    await query.answer()
    await query.edit_message_text("Are you sure you want to delete this package? Type 'yes' to confirm or 'no' to cancel.")
    return CONFIRM_DELETE

# Function to confirm and delete the package
async def confirm_delete(update: Update, context: CallbackContext) -> int:
    user_response = update.message.text.lower()
    
    if user_response == 'yes':
        package_id = context.user_data['package_id']
        try:
            async with AsyncSessionLocal() as conn:
                await conn.execute(
                    sqlalchemy.text(
                        "DELETE FROM token_packages WHERE package_id = :package_id"
                    ).params(package_id=package_id)
                )
                await conn.commit()

            await update.message.reply_text("Package deleted successfully!")
        except Exception as e:
            await update.message.reply_text(f"An error occurred: {e}")
    else:
        await update.message.reply_text("Package deletion canceled.")

    return ConversationHandler.END

# Function to handle the cancelation
async def cancel(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text("Action canceled.")
    return ConversationHandler.END


###########################################################################################################################################################   
# Purchase tokens

SELECTING_PACKAGE, PHOTO_REQUESTED = range(2)

async def purchase_tokens(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Entry command for user to purchase token packages
    Provides user with package details

    Args:
        update (Update): _description_
        context (ContextTypes.DEFAULT_TYPE): _description_

    Returns:
        int: returns new state for convo handler
    """    
    # Retrieve token packages from the database
    async with AsyncSessionLocal() as conn:
        results = await conn.execute(
            sqlalchemy.text(
                "SELECT package_id, package_name, number_of_tokens, price, description, validity FROM token_packages"
            )
        )
        token_packages = results.fetchall()

    # Format packages as inline buttons
    keyboard = []
    package_info = "<u><b>Packages:</b></u>\n\n"
    for package in token_packages:
        package_id, package_name, tokens, price, description, validity = package
        package_info += f"<b>{package_name}</b>:\n{description}\nTokens are valid for {validity} days\n\n"
        keyboard.append([InlineKeyboardButton(package_name, callback_data=f"select_package|{package_id}")])

    # Check if there are no packages available
    if not token_packages:
        await update.message.reply_text('No token packages available.')
        return ConversationHandler.END

    # Add static package info
    package_info += "<b><u>Token Usage</u></b>\n\n"
    package_info += "<b>Part Time Job Post (+3 shortlists):</b> 45 tokens \n<b>Full Time Job Post (+3 shortlists):</b> 70 tokens \n<b>Reposting of jobs (+3 shortlist):</b> 30 tokens \n<b>3 additional shortlist: </b>5 tokens\n\nPlease select a package:"
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(package_info, reply_markup=reply_markup, parse_mode='HTML')

    return SELECTING_PACKAGE

SELECTING_SUBSCRIPTION, SUBSCRIPTION_PHOTO_REQUESTED = range(2)
async def purchaseSubscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Entry command for user to purchase sub packages
    Provides user with sub details

    Args:
        update (Update): _description_
        context (ContextTypes.DEFAULT_TYPE): _description_

    Returns:
        int: returns new state for convo handler
    """    
    chat_id = update.effective_chat.id
    # Check if chat_id is already in an active subscription plan
    query_string = '''
    SELECT EXISTS (
    SELECT 1
    FROM subscription_balance
    WHERE chat_id = :chat_id AND status = 'active')
    '''
    params = {"chat_id": chat_id}
    results = await safe_get_db(query_string, params)
    already_subscribed = results[0][0]
    if already_subscribed:
        # Get current plan details from subscription_balance
        query_string = "SELECT end_date FROM subscription_balance WHERE chat_id = :chat_id AND status = 'active' ORDER BY end_date DESC LIMIT 1"
        params = {"chat_id": chat_id}
        results = await safe_get_db(query_string, params)
        end_date = results[0][0]
        await update.message.reply_text(f"You are already in an active subscription plan which ends on {end_date}.\nAny new subscription plans will only begin after the current one has run out.")

    # Retrieve token packages from the database
    context.user_data['chat_id'] = update.effective_chat.id
    async with AsyncSessionLocal() as conn:
        results = await conn.execute(
            sqlalchemy.text(
                "SELECT subpkg_code, sub_name, number_of_tokens, duration_months, price FROM subscription_packages"
            )
        )
        subscription_packages = results.fetchall()
    

    # Format packages as inline buttons
    keyboard = []
    package_info = "<u><b>Subscription Packages:</b></u>\n\n"
    for package in subscription_packages:
        subpkg_id, sub_name, tokens_per_month, duration_months, price = package
        package_info += f"<b>{sub_name}</b>:\n${price} - {tokens_per_month} tokens/month for {duration_months} months.\n\n"
        keyboard.append([InlineKeyboardButton(sub_name, callback_data=f"select_subscription|{subpkg_id}")])

    # Check if there are no packages available
    if not subscription_packages:
        await update.message.reply_text('No subscription packages available.')
        return ConversationHandler.END

    # Add static package info
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(package_info, reply_markup=reply_markup, parse_mode='HTML')

    return SELECTING_PACKAGE

async def subscription_selection(update: Update, context: CallbackContext) -> int:
    """
    Saves chosen subscription in context.user_data['selected_subscription_id']
    If user is already in active sub, it will extend the sub 


    Args:
        update (Update): _description_
        context (CallbackContext): _description_

    Returns:
        int: State for convo handler
    """    
    query = update.callback_query
    await query.answer()
    subpkg_id = query.data.split('|')[1]
    chat_id = context.user_data['chat_id']
    # Check if chat_id is already in an active subscription plan
    query_string = '''
    SELECT EXISTS (
    SELECT 1
    FROM subscription_balance
    WHERE chat_id = :chat_id AND status = 'active')
    '''
    params = {"chat_id": chat_id}
    results = await safe_get_db(query_string, params)
    already_subscribed = results[0][0]
    
    if already_subscribed:
        # Get current plan details from subscription_balance
        query_string = "SELECT end_date FROM subscription_balance WHERE chat_id = :chat_id AND status = 'active'"
        params = {"chat_id": chat_id}
        results = await safe_get_db(query_string, params)
        end_date = results[0][0]
        # await query.edit_message_text(f"You are already in an active subscription plan which ends on {end_date}.\nAny new subscription plans will only begin after the current one has run out.")
        # return ConversationHandler.END
    # Store the selected package_id in the user context
    context.user_data['selected_package_id'] = subpkg_id

    # Fetch package details from the database
    query_string = "SELECT sub_name, number_of_tokens, duration_months, price FROM subscription_packages WHERE subpkg_code = :subpkg_id"
    params = {"subpkg_id": subpkg_id}
    results = await safe_get_db(query_string, params)
    subscription_details = results[0]
    if subscription_details:
        sub_name, tokens_per_month, duration_months, price = subscription_details
        context.user_data['sub_name'] = sub_name
        context.user_data['tokens_per_month'] = tokens_per_month
        context.user_data['duration_months'] = duration_months
        context.user_data['price'] = price

        confirmation_message = f"You have picked the subscription package: {sub_name}\n\nYou are required to pay ${price} for {tokens_per_month} tokens per month for {duration_months} months.\n\nPlease make the payment and send a screenshot."
        await query.edit_message_text(confirmation_message)

        # Send a photo
        photo_path = "paynow_qrcode.jpg"
        await query.message.reply_photo(photo=open(photo_path, 'rb'))
        
    # return ConversationHandler.END
    return SUBSCRIPTION_PHOTO_REQUESTED


async def package_selection(update: Update, context: CallbackContext) -> int:
    """
     Saves chosen package in context.user_data['selected_package_id']

    Args:
        update (Update): _description_
        context (CallbackContext): _description_

    Returns:
        int: State for convo handler
    """    
    query = update.callback_query
    await query.answer()
    package_id = query.data.split('|')[1]

    # Store the selected package_id in the user context
    context.user_data['selected_package_id'] = package_id

    # Fetch package details from the database
    async with AsyncSessionLocal() as conn:
        results = await conn.execute(
            sqlalchemy.text(
                "SELECT package_name, number_of_tokens, price, description FROM token_packages WHERE package_id = :package_id"
            ).params(package_id=package_id)
        )
        package = results.fetchone()

    if package:
        package_name, tokens, price, description = package
        context.user_data['package_price'] = price
        context.user_data['package_tokens'] = tokens
        context.user_data['package_description'] = description

        confirmation_message = f"You have picked the package: {description}\n\nYou are required to pay ${price} for {tokens} tokens.\n\nPlease make the payment and send a screenshot."
        await query.edit_message_text(confirmation_message)

        # Send a photo
        photo_path = "paynow_qrcode.jpg"
        await query.message.reply_photo(photo=open(photo_path, 'rb'))
        
    # return ConversationHandler.END
    return PHOTO_REQUESTED

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

async def verifyPayment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Requests for screenshot of payment from Agency
    Calls create_transaction_entry() upon recieving screenshot
    Then calls the forward_to_admin_for_acknowledgement()

    Args:
        package_id: Package ID which agency wants to purchase
    """    
    chat_id = update.effective_chat.id
    package_id = context.user_data['selected_package_id']
    # Check if subscription package
    if 's' in package_id:
        isSubscription = True
    else:
        isSubscription = False
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
        return await forward_to_admin_for_acknowledgement(update, context, photo=photo, transaction_id=transaction_id)
    else:
        await update.message.reply_text(
            "Please upload a screenshot."
        )
        return PHOTO_REQUESTED

async def forward_to_admin_for_acknowledgement(update: Update, context: ContextTypes.DEFAULT_TYPE, photo=None, transaction_id=None, message=None, job_post_id=None):
    """
    Send a copy of the message/screenshot to an admin for acknowledgement.
    Users can continue interacting with the bot while waiting for response

    Args:
        update (Update): _description_
        context (ContextTypes.DEFAULT_TYPE): _description_
        photo: Screenshot to forwrd
        message: Job Post message to forward
        transaction_id: Transaction ID of payment
        job_post_id: 
        id: Transaction ID if payment (image) or Job Posts ID if post (text)
    """    
    logger.info(f"forward_to_admin_for_acknowledgement() called, forwarding to ADMIN USER {ADMIN_CHAT_ID}")
    # Handling token purchase screenshots
    if photo:
        logger.info("Forwarding screenshot to admin for approval")
        query_string = f"SELECT chat_id, package_id FROM transactions WHERE transaction_id = '{transaction_id}'"
        results = await get_db(query_string)
        logger.info(f"Results: {results}")
        chat_id, package_id = results[0] # unpack tuple
        if 's' in package_id:
            isSubscription = True
        else:
            isSubscription = False
        # Set callback data from ID provided
        ss_accept_callback_data = f"ss_accept_{transaction_id}" # Callbackdata has fixed format: ss_<transaction_ID> (screenshot) or jp_<transaction_ID> (job post)
        ss_reject_callback_data = f"ss_reject_{transaction_id}"
        logger.info(f"Callback data: {ss_accept_callback_data}, {ss_reject_callback_data}")
        keyboard = [
            [InlineKeyboardButton("Approve", callback_data=ss_accept_callback_data)],
            [InlineKeyboardButton("Reject", callback_data=ss_reject_callback_data)]
        ] # Can check transaction ID if need details
        reply_markup = InlineKeyboardMarkup(keyboard)
        # Get user handle from chat id
        query_string = "SELECT user_handle FROM user_data WHERE chat_id = :chat_id"
        params = {"chat_id": chat_id}
        results = await safe_get_db(query_string, params)
        # user_handle = results[0][0]
        # user_handle = results[0][0]
        if not results: # from group chat idk?
            logger.info("results are empty")
            user_handle = chat_id
        else:
            user_handle = results[0][0]
        if isSubscription:
            # Get sub package details
            query_string = "SELECT sub_name, number_of_tokens, duration_months, price FROM subscription_packages WHERE subpkg_code = :subpkg_code"
            params = {"subpkg_code": package_id}
            results = await safe_get_db(query_string, params)
            sub_name, tokens_per_month, duration_months, price = results[0]
            caption = f"Dear Admin, {user_handle} wants to purchase the Subscription Package: {sub_name} for ${price}\nThey will be allocated {tokens_per_month} tokens for {duration_months} months."
        else:
            query_string = "SELECT package_name, number_of_tokens, price, validity FROM token_packages WHERE package_id = :package_id"
            params = {"package_id": package_id}
            results = await safe_get_db(query_string, params)
            package_name, number_of_tokens, price, validity = results[0]
            caption = f"Dear Admin, {user_handle} wants to purchase the Subscription Package: {package_name} for ${price}"
        await context.bot.send_photo(
            chat_id=ADMIN_CHAT_ID,
            photo=photo,
            caption=caption,
            reply_markup=reply_markup
        )
        await update.message.reply_text(
        "Photo forwarded to admin."
    )

    # Handling job posts
    elif message:
        logger.info("Forwarding job post message to admin for approval")
        # Set callback data from ID provided
        jp_accept_callback_data = f"jp_accept_{job_post_id}" # Callbackdata has fixed format: ss_<transaction_ID> (screenshot) or jp_<transaction_ID> (job post)
        jp_reject_callback_data = f"jp_reject_{job_post_id}"
        keyboard = [
            [InlineKeyboardButton("Approve", callback_data=jp_accept_callback_data)],
            [InlineKeyboardButton("Reject", callback_data=jp_reject_callback_data)]
        ] # Can check transaction ID if need details
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text="There is a new job posting pending your approval:")
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=message,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
        await update.callback_query.message.reply_text(
            'Please note the following:\n\n'
            '1. No MLM jobs\n'
            '2. No SingPass required jobs\n'
            '3. If scam jobs are found, the job post will be deleted, and credits will be revoked without a refund.\n\n'
            'Your job posting has been forwarded to the admin. You will be informed when it has been approved.')

    # End the conversation
    return ConversationHandler.END
    




async def get_admin_acknowledgement(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    CallbackHandler for the acknowledgement button when messages or screenshots are forwarded to the admin.

    Request admin to approve/reject transaction.
    If transaction is approved, calls update_balance() for chat_id to allocate credits.
    Alerts users when credits are allocated.

    This gets called when the button is pressed.
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
    # if query_data.startswith('sp_'):
    #     logger.info("Subscription package query found")
    #     # Getting admin response as well as transaction ID
    #     status, transaction_id = query_data.split('_')[1:]


    if query_data.startswith('ss_'): 
        logger.info("SS query found")
        # Getting admin response as well as transaction ID
        status, transaction_id = query_data.split('_')[1:]
        # Select chat_id based on transaction_id
        query_string = f"SELECT chat_id, package_id FROM transactions WHERE transaction_id = '{transaction_id}'"
        results = await get_db(query_string)
        logger.info(f"Results: {results}")
        chat_id, package_id = results[0]
        if 's' in package_id:
            isSubscription = True
        else:
            isSubscription = False
        # # Get chat id from agency id
        # query_string = f"SELECT chat_id FROM agencies WHERE id = '{agency_id}'"
        # results = await get_db(query_string)
        # chat_id = results[0][0]

        if status == 'accept':
            # Update transaction entry status to 'Approved'
            query_string = f"UPDATE transactions SET status = 'Approved' WHERE transaction_id = '{transaction_id}'"
            await set_db(query_string)
            logger.info(f"Approved {transaction_id} in database!")
            if not isSubscription:
                # Update balance of user account
                (new_balance, exp_date) = await update_balance(chat_id=chat_id, package_id=package_id)
                exp_date = exp_date.date()
                await query.answer()  # Acknowledge the callback query to remove the loading state
            elif isSubscription:
                # Check if has existing subs
                # chat_id = update.effective_chat.id
                # Check if chat_id is already in an active subscription plan
                query_string = '''
                SELECT EXISTS (
                SELECT 1
                FROM subscription_balance
                WHERE chat_id = :chat_id AND status = 'active')
                '''
                params = {"chat_id": chat_id}
                results = await safe_get_db(query_string, params)
                already_subscribed = results[0][0]
                if already_subscribed:
                    await add_active_subscription(chat_id, package_id)
                    await query.edit_message_caption(caption="You have approved the payment.\n\nCredits have been transferred.")
                    await context.bot.send_message(chat_id=chat_id, text="Your payment has been acknowledged by an admin!")
                    return

                else:
                # Top up once and top up every month
                    (new_balance, exp_date) = await update_balance_subscription(chat_id, package_id)
                    await add_active_subscription(chat_id, package_id)
            
            # Edit the caption of the photo message
            await query.edit_message_caption(caption="You have approved the payment.\n\nCredits have been transferred.")
            
            # Notify the user
            await context.bot.send_message(chat_id=chat_id, text=f"Your payment has been acknowledged by an admin!.\n\nYour new token balance is: {new_balance}\nExpiring on: {exp_date}")
        
        elif status == 'reject':
            # Update transaction entry status to 'Rejected'
            query_string = f"UPDATE transactions SET status = 'rejected' WHERE transaction_id = '{transaction_id}'"
            await set_db(query_string)
            logger.info(f"Rejected {transaction_id} in database!")
            await query.answer()  # Acknowledge the callback query to remove the loading state

            
            # Edit the caption of the photo message
            await query.edit_message_caption(caption="You have rejected the screenshot.\n\nUser will be notified")
            
            # Notify the user
            await context.bot.send_message(chat_id=chat_id, text="Your payment has been rejected by an admin. Please PM @jojoweipop for more details")

    elif query_data.startswith('jp_'): 
        logger.info("JP query found")
        # Getting admin response as well as job ID
        status, job_post_id = query_data.split('_')[1:]
        # Check if repost
        repost = False
        query_string = '''
        SELECT EXISTS (
        SELECT 1
        FROM job_posts
        WHERE id = :job_post_id AND status = 'approved')
        '''
        params = {"job_post_id": job_post_id}
        results = await safe_get_db(query_string, params)
        repost = results[0][0]

        # Check if part time
        query_string = '''
        SELECT EXISTS (
        SELECT 1
        FROM job_posts
        WHERE id = :job_post_id AND job_type = 'part')
        '''
        params = {"job_post_id": job_post_id}
        results = await safe_get_db(query_string, params)
        part_time = results[0][0]
        # Get agency id based on job id
        query_string = f"SELECT agency_id FROM job_posts WHERE id = '{job_post_id}'"
        results = await get_db(query_string)
        agency_id = results[0][0]
        # Get chat id from agency id 
        query_string = f"SELECT chat_id FROM agencies WHERE id = '{agency_id}'"
        results = await get_db(query_string)
        chat_id = results[0][0]


        if status == 'accept':
            if not repost: # if not repost
                # Update job post status to 'Approved'
                query_string = f"UPDATE job_posts SET status = 'Approved' WHERE id = '{job_post_id}'"
                await set_db(query_string)
                logger.info(f"Approved {job_post_id} in database!")
            # Post to channel
            message = await draft_job_post_message(job_post_id, repost=repost, part_time=part_time)
            await post_job_in_channel(update, context, message=message, job_post_id=job_post_id)
            # Add 3 shortlist to user chat_id
            num_shortlists = 3
            query_shortlists = "SELECT shortlist FROM shortlist_balance WHERE chat_id = :chat_id"
            shortlists_result = await safe_get_db(query_shortlists, {"chat_id": chat_id})
                
            # Check if entry for chat_id in shortlist_balance table
            context.user_data['entry_present'] = 1 if shortlists_result else 0
            # If have a chat_id entry in the shortlist_balance table, update value
            if context.user_data['entry_present']:
                update_shortlists_query = "UPDATE shortlist_balance SET shortlist = shortlist + :new_shortlists WHERE chat_id = :chat_id"
                await safe_set_db(update_shortlists_query, {"chat_id": chat_id, "new_shortlists": num_shortlists})
            #Else, create new entry in the shortlist_balance table
            else:
                insert_shortlists_query = "INSERT INTO shortlist_balance (chat_id, shortlist) VALUES (:chat_id, :new_shortlists)"
                await safe_set_db(insert_shortlists_query, {"chat_id": chat_id, "new_shortlists": num_shortlists})

            # Alert user of approval
            await query.answer()  # Acknowledge the callback query to remove the loading state
            
            # Edit the caption of the photo message
            await query.edit_message_text(text="You have approved this Job Posting.\n\nAgency will be notifed.")
            
            # Notify the user
            await context.bot.send_message(chat_id=chat_id, text=f"Your posting has been approved by the admin!.\n\nIt has been posted in the channel with Job ID: {job_post_id}")
        
        elif status == 'reject':

            if not repost: # if not reposting
                if part_time:
                    tokens_to_deduct = PART_JOB_POST_PRICE
                    
                else:
                    tokens_to_deduct = JOB_POST_PRICE
                # Update job post status to 'Rejected'
                query_string = f"UPDATE job_posts SET status = 'Rejected' WHERE id = '{job_post_id}'"
                await set_db(query_string)
                logger.info(f"Rejected {job_post_id} in database!")
            if repost:
                tokens_to_deduct = JOB_REPOST_PRICE

            # Give user back credits
            query_string = "SELECT EXISTS (SELECT 1 FROM token_balance WHERE chat_id = :chat_id)"
            params = {"chat_id": chat_id}
            results = await safe_get_db(query_string, params)
            have_entry = results[0][0]
            # There is an entry in the table
            logger.info(f"HAVE ENTRY: {have_entry}")
            if have_entry: 
                logger.info("entry exists in table")
                # Give back credits
                query_string = "UPDATE token_balance SET tokens = tokens + :price_of_job_post WHERE chat_id = :chat_id"
                params = {
                    "price_of_job_post": tokens_to_deduct,
                    "chat_id": chat_id
                }
                await safe_set_db(query_string, params)
            else: # Dont refund if it would have expired
                logger.info("CREDITS EXPIRED, NO REFUND")

            await query.answer()  # Acknowledge the callback query to remove the loading state
            
            # Edit the caption of the photo message
            if repost: 
                text = "You have rejected the repost.\n\nAgency will be notified and tokens refunded."
            else:
                text = "You have rejected the Job Posting.\n\nAgency will be notified and tokens refunded."
            await query.edit_message_text(text=text)
            
            # Notify the user
            if repost:
                text = "Your repost has been rejected by an admin. Please PM @jojoweipop for more details"
            else:
                text = "Your posting has been rejected by an admin. Please PM @jojoweipop for more details"
            await context.bot.send_message(chat_id=chat_id, text=text)

async def add_active_subscription(chat_id, package_id):
    '''
    Assumes first month is already paid for, this will add to subscription_balance and set last distribution to today.
    '''
    # Checks if it is a subscription package
    if 's' in package_id:
        pass
    else:
        raise Exception("Package is not a subscription")
    # Get subs package details
    query_string = "SELECT sub_name, number_of_tokens, duration_months, price FROM subscription_packages WHERE subpkg_code = :package_id"
    params = {"package_id": package_id}
    results = await safe_get_db(query_string, params)
    package_details = results[0]
    sub_name, tokens_per_month, duration_months, price = package_details
    curr_date = datetime.now().date()
    # Check if has existing subscription
    query_string = """
    SELECT EXISTS (
        SELECT 1
        FROM subscription_balance
        WHERE chat_id = :chat_id
        AND status = 'active'
    ) AS entry_exists;
    """
    params = {"chat_id": chat_id}
    results = await safe_get_db(query_string, params)
    have_existing_sub = results[0][0]
    if have_existing_sub:
        # Find the last date for existing subs
        query_string = "SELECT end_date FROM subscription_balance WHERE chat_id = :chat_id AND status = 'active' ORDER BY end_date DESC LIMIT 1"
        params = {"chat_id": chat_id}
        results = await safe_get_db(query_string, params)
        current_end_date = results[0][0]
        # Set start date of new sub to the day after current end date
        start_date = current_end_date + relativedelta(days=1)
    else:
        start_date = curr_date
    # Create new entry
    query_string = "INSERT INTO subscription_balance (chat_id, start_date, end_date, last_distribution, status, subpkg_id) VALUES (:chat_id, :start_date, :end_date, :last_distribution, :status, :subpkg_id)"
    end_date = start_date + relativedelta(months=duration_months)
    last_distribution = curr_date
    status = 'active'
    params = {
        "chat_id": chat_id,
        "start_date": start_date,
        "end_date": end_date,
        "last_distribution": last_distribution,
        "status": status,
        "subpkg_id": package_id
    }
    await safe_set_db(query_string, params)
    return True

async def update_balance_subscription(chat_id, package_id):
    '''
    Checks details of sub pacakge and gives user first month of payment
    '''
    # Checks if it is a subscription package
    if 's' in package_id:
        isSubscription = True
    else:
        raise Exception("Package is not a subscription")
    # Get subs package details
    query_string = "SELECT sub_name, number_of_tokens, duration_months, price FROM subscription_packages WHERE subpkg_code = :package_id"
    params = {"package_id": package_id}
    results = await safe_get_db(query_string, params)
    package_details = results[0]
    sub_name, tokens_per_month, duration_months, price = package_details
    # Give one month of tokens first, with expiry being one month as well

    # Check if user chat_id has row in token_balance table for db
    query_string = f"SELECT EXISTS (SELECT 1 FROM token_balance WHERE chat_id = '{chat_id}')"
    results = await get_db(query_string)
    # Calculate new expiry date
    curr_date = datetime.now()
    new_date = curr_date + relativedelta(months=1) #! hardcoded package expiry to be each month
    # If have existing entry
    logger.info(f"Chat ID is already in token_balance table: {results}")
    if (results[0][0]):
        # Get balance and current expiry date of tokens
        query_string = f"SELECT tokens, exp_date FROM token_balance WHERE chat_id = '{chat_id}'"
        results = await get_db(query_string)
        curr_tokens, curr_exp_date = results[0]
        # Calculate new tokens
        new_balance = curr_tokens + tokens_per_month
        # If extended date is longer than current exp date, updates both token balance and exp date of chat_id
        if new_date > curr_exp_date:
            query_string = f"UPDATE token_balance SET tokens = '{new_balance}', exp_date = '{new_date}' WHERE chat_id = '{chat_id}'"

        # Otherwise, dont touch exp date
        else:
            query_string = f"UPDATE token_balance SET tokens = '{new_balance}' WHERE chat_id = '{chat_id}'"
            new_date = curr_exp_date # Just for returning the expiry date, new_date is not used here
        # Update db
        await set_db(query_string)
        return new_balance, new_date
    # If no existing entry, create new entry
    else: 
        # Create new entry
        query_string = f"INSERT INTO token_balance (chat_id, tokens, exp_date) VALUES ('{chat_id}', '{tokens_per_month}', '{new_date}')"
        await set_db(query_string)
        logger.info("Chat ID does not exist in token_balance table")
        return tokens_per_month, new_date

async def update_balance(chat_id, package_id):
    """
    Updates account balance in database for newly purchased package
    Returns:
        Updated balance of account
    """

    # Check number of tokens and validity of purchased package, validity is in days
    query_string = f"SELECT number_of_tokens,validity FROM token_packages WHERE package_id = '{package_id}'"
    results = await get_db(query_string)
    package_tokens, validity = results[0]
    # Check if user chat_id has row in token_balance table for db
    query_string = f"SELECT EXISTS (SELECT 1 FROM token_balance WHERE chat_id = '{chat_id}')"
    results = await get_db(query_string)
    # Calculate new expiry date
    curr_date = datetime.now()
    new_date = curr_date + timedelta(days=validity)
    # If have existing entry
    logger.info(f"Chat ID is already in token_balance table: {results}")
    if (results[0][0]):
        # Get balance and current expiry date of tokens
        query_string = f"SELECT tokens, exp_date FROM token_balance WHERE chat_id = '{chat_id}'"
        results = await get_db(query_string)
        curr_tokens, curr_exp_date = results[0]
        # Calculate new tokens
        new_balance = curr_tokens + package_tokens
        # If extended date is longer than current exp date, updates both token balance and exp date of chat_id
        if new_date > curr_exp_date:
            query_string = f"UPDATE token_balance SET tokens = '{new_balance}', exp_date = '{new_date}' WHERE chat_id = '{chat_id}'"

        # Otherwise, dont touch exp date
        else:
            query_string = f"UPDATE token_balance SET tokens = '{new_balance}' WHERE chat_id = '{chat_id}'"
            new_date = curr_exp_date # Just for returning the expiry date, new_date is not used here
        # Update db
        await set_db(query_string)
        return new_balance, new_date
    # If no existing entry, create new entry
    else: 
        # Create new entry
        query_string = f"INSERT INTO token_balance (chat_id, tokens, exp_date) VALUES ('{chat_id}', '{package_tokens}', '{new_date}')"
        await set_db(query_string)
        logger.info("Chat ID does not exist in token_balance table")
        return package_tokens, new_date






###########################################################################################################################################################
#? Misc Commands
async def view_tokens(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    View tokens
    """
    # Get token balance with chat_id
    chat_id = update.effective_chat.id
    query_string = "SELECT tokens, exp_date FROM token_balance WHERE chat_id = :chat_id"
    params = {"chat_id": chat_id}
    
    # Check if user chat_id has row in token_balance table for db
    query_string = f"SELECT EXISTS (SELECT 1 FROM token_balance WHERE chat_id = '{chat_id}')"
    results = await get_db(query_string)
    # If have existing entry
    logger.info(f"Chat ID is already in token_balance table: {results}")
    if (results[0][0]):
        # Get balance and current expiry date of tokens
        query_string = f"SELECT tokens, exp_date FROM token_balance WHERE chat_id = '{chat_id}'"
        results = await get_db(query_string)
        curr_tokens, curr_exp_date = results[0]
        # Notify user
        await update.message.reply_text(text=f"You have {curr_tokens} tokens expiring on {curr_exp_date.date()}.")

    else: 
        await update.message.reply_text(text="You have no tokens available.")
















# Get Grp ChatID and send message to group
async def get_chat_id(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"The chat ID is: {chat_id}")

# async def send_message_to_group(update: Update, context: CallbackContext) -> None:
#     # Replace 'YOUR_CHAT_ID' with the actual chat ID of the group
#     chat_id = 'YOUR_CHAT_ID'
#     message = 'Hello, group! This is a message from the bot.'
#     await bot.send_message(chat_id=chat_id, text=message)





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
async def global_error_handler(update, context):
    """Handles and logs any unexpected errors."""

    # Log the error
    logger.info(f"Update {update} caused error {context.error}")
    traceback.print_exc()

    # Optionally, notify the developer or admin
    # await context.bot.send_message(chat_id=CHANNEL_ID, text=f"An error occurred: {context.error}")

# Bot classes

###########################################################################################################################################################   
# Function to check and update expired credits
async def daily_checks(bot):
    # remove expired credits
    try:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        query_string = f"SELECT chat_id, tokens FROM token_balance WHERE exp_date <= '{now}'"
        results = await get_db(query_string)
        logger.info(f"Checking expiring tokens at {now}")
        for entries in results:
            chat_id, expiring_tokens = entries
            #remove expired entry
            query_string = f"DELETE from token_balance WHERE chat_id = '{chat_id}'"
            await set_db(query_string)
            logger.info(f"Removed {expiring_tokens} expired tokens from {chat_id} account")
            # Get tele handle from chat_id
            query_string = "SELECT user_handle FROM user_data WHERE chat_id = :chat_id"
            params = {"chat_id": chat_id}
            results = await safe_get_db(query_string, params)
            user_handle = results[0][0]

            # notify users that their credits have expired (send to admin as well)
            await bot.send_message(chat_id=chat_id, text=f"{expiring_tokens} tokens have expired today!\n\nTo purchase more tokens, please use the /purchase_tokens command!")
            await bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"{expiring_tokens} tokens has expired from {user_handle}'s account.")
    except Exception as e:
        logger.info(e)
    # check active subscriptions
    try:
        # get active subscriptions
        now = datetime.now()
        logger.info("Getting active subs:")
        query_string = "SELECT id, chat_id, start_date, end_date, last_distribution, subpkg_id FROM subscription_balance WHERE status = :status"
        params = {"status": 'active'}
        active_subs = await safe_get_db(query_string, params)
        # chat_id, end_date, last_distribution, subpkg_id = active_subs
        # Get tokens_per_month from subscription_packages with subpkg_id



        for sub_balance_id, chat_id, start_date, end_date, last_distribution, subpkg_id in active_subs:
            # if expired
            if now >= end_date:
                # Set status to expired
                query_string = "UPDATE subscription_balance SET status = :status WHERE id = :sub_balance_id"
                params = {"status": "expired", "sub_balance_id": sub_balance_id}
                await safe_set_db(query_string, params)
                continue # goes to next subscription

            # if active subscription
            query_string = "SELECT sub_name, number_of_tokens, duration_months FROM subscription_packages WHERE subpkg_code = :subpkg_code"
            params = {"subpkg_code": subpkg_id}
            results = await safe_get_db(query_string, params)
            sub_name, tokens_per_month, duration_months = results[0]

            # Calculate the next distribution date
            next_distribution_date = last_distribution + relativedelta(months=1)
            
            if (now >= next_distribution_date) and (now >= start_date):
                logger.info(f"Allocating tokens for subscription id: {sub_balance_id}")
                # Set last distribution_date
                query_string = "UPDATE subscription_balance SET last_distribution = :last_distribution WHERE id = :id"
                params = {
                    "last_distribution": now,
                    "id": sub_balance_id
                    }
                await safe_set_db(query_string, params)
                
                # Check if user chat_id has row in token_balance table for db
                query_string = f"SELECT EXISTS (SELECT 1 FROM token_balance WHERE chat_id = '{chat_id}')"
                results = await get_db(query_string)
                # Calculate new expiry date
                curr_date = now
                new_date = curr_date + relativedelta(months=1)
                # If have existing entry
                logger.info(f"Chat ID is already in token_balance table: {results}")
                if (results[0][0]):
                    # Get balance and current expiry date of tokens
                    query_string = f"SELECT tokens, exp_date FROM token_balance WHERE chat_id = '{chat_id}'"
                    results = await get_db(query_string)
                    curr_tokens, curr_exp_date = results[0]
                    # Calculate new tokens
                    new_balance = curr_tokens + tokens_per_month
                    # If extended date is longer than current exp date, updates both token balance and exp date of chat_id
                    if new_date > curr_exp_date:
                        query_string = f"UPDATE token_balance SET tokens = '{new_balance}', exp_date = '{new_date}' WHERE chat_id = '{chat_id}'"
                    # Otherwise, dont touch exp date
                    else:
                        query_string = f"UPDATE token_balance SET tokens = '{new_balance}' WHERE chat_id = '{chat_id}'"
                        new_date = curr_exp_date # Just for returning the expiry date, new_date is not used here
                    # Update db
                    await set_db(query_string)
                    await bot.send_message(chat_id=chat_id, text=f"{tokens_per_month} tokens have been allocated to your account.\nYour have a new balance of {new_balance}, expiring on {new_date.date()}.")

                    # return new_balance, new_date
                # If no existing entry, create new entry
                else: 
                    # Create new entry
                    query_string = f"INSERT INTO token_balance (chat_id, tokens, exp_date) VALUES ('{chat_id}', '{tokens_per_month}', '{new_date}')"
                    await set_db(query_string)
                    logger.info("Chat ID does not exist in token_balance table")
                    await bot.send_message(chat_id=chat_id, text=f"{tokens_per_month} tokens have been allocated to your account.\nYour have a new balance of {tokens_per_month}, expiring on {new_date.date()}.")

                    # return tokens_per_month, new_date
    except Exception as e:
        logger.info(e)

# async def test_schedule(bot):
#     logger.info(f"test_schedule called at {datetime.now()}")
#     query_string = "DELETE FROM agencies WHERE id = '8beb109a-45b2-11ef-9d12-42010a400005'"
#     if await set_db(query_string):
#         logger.info("DELETED!")
#     await bot.send_message(chat_id=ADMIN_CHAT_ID, text="Your agency account has been deleted!")

# Function to run scheduled tasks
async def run_schedule():
    while True:
        schedule.run_pending()
        await asyncio.sleep(1)

# Main
async def main() -> None:
    """Set up PTB application and a web application for handling the incoming requests."""
    context_types = ContextTypes(context=CustomContext)
    # Here we set updater to None because we want our custom webhook server to handle the updates
    # and hence we don't need an Updater instance

    application = (
        Application.builder().token(BOT_TOKEN).updater(None).context_types(context_types).build()
    )

    # Command handlers
    application.add_handler(CommandHandler('viewtokens', view_tokens))
    application.add_handler(CommandHandler('create_trans', create_transaction_entry))
    # application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help))
    # application.add_handler(CommandHandler("register", register))
    application.add_handler(CommandHandler("deleteprofile", delete_profile))
    application.add_handler(CommandHandler("viewprofile", view_profile))
    application.add_handler(CommandHandler('get_chat_id', get_chat_id))
    # application.add_handler(CommandHandler('send_message_to_group', send_message_to_group))



    # #TODO remove since combined with purchase_tokens_handler
    # # Payment Convo Handler 
    # payment_handler = ConversationHandler(
    #     entry_points=[CommandHandler('verifypayment', verifyPayment)],
    #     states={
    #         PHOTO_REQUESTED: [MessageHandler(filters.PHOTO, verifyPayment)]
    #     },
        
    #     fallbacks=[CommandHandler('cancel', cancel)]
    # )
    # application.add_handler(payment_handler)

    # Registration Convo Handler
    registration_conversation_handler = ConversationHandler(
        entry_points=[
            CommandHandler('start', start),
            CommandHandler('register', start)
        ],
        states={
        NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_for_dob),CallbackQueryHandler(register_button, pattern='^(applicant|agency)$')],
        DOB: [MessageHandler(filters.TEXT & ~filters.COMMAND, validate_dob)],
        PAST_EXPERIENCES: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_text_handler)],
        CITIZENSHIP: [CallbackQueryHandler(citizenship_button)],
        RACE: [CallbackQueryHandler(race_button)],
        GENDER: [CallbackQueryHandler(gender_button)],
        HIGHEST_EDUCATION: [CallbackQueryHandler(highest_education_button)],
        LANGUAGES_SPOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_text_handler)],
        WHATSAPP_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_text_handler)],
        FULL_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_text_handler)],
        COMPANY_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_text_handler)],
        COMPANY_UEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_text_handler)]
    },
        fallbacks=[CommandHandler('cancel', cancel)])
    
    application.add_handler(registration_conversation_handler)



    # Edit profile convo handler
    edit_profile_convo_handler = ConversationHandler(
        entry_points=[CommandHandler('editprofile', edit_profile)],
        states={
            SELECT_PROFILE: [
                CallbackQueryHandler(select_profile, pattern='^edit_profile\\|')],
            SELECT_ATTRIBUTE: [
                CallbackQueryHandler(select_attribute, pattern='^edit_attribute\\|')],
            ENTER_NEW_VALUE: [
                CallbackQueryHandler(enter_new_value, pattern='^(Singaporean|Permanent Resident\\(PR\\)|Student Pass|Foreign Passport Holder|Chinese|Malay|Indian|Eurasian|Others|Male|Female|O-level Graduate|ITE Graduate|A-level Graduate|Diploma Graduate|Degree Graduate|Undergraduate|Studying in Poly/JC)$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_new_value)]
        },
    fallbacks=[CommandHandler('cancel', cancel)]
    )
    application.add_handler(edit_profile_convo_handler)


# Job post convo handler
    job_post_handler = ConversationHandler(
    entry_points=[
        CommandHandler('jobpost', job_post), 
        # CallbackQueryHandler(post_a_job_button, pattern='post_a_job')
        ],
    states={
        # SELECT_AGENCY: [CallbackQueryHandler(jobpost_button, pattern='^jobpost')],
        SELECT_AGENCY: [CallbackQueryHandler(jobpost_button)],
        ENTER_JOB_TYPE: [CallbackQueryHandler(job_type_selection, pattern='^job_type_')],
        ENTER_JOB_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, jobpost_text_handler)],
        ENTER_OTHER_REQ: [
            CallbackQueryHandler(jobpost_additional_req, pattern='^(yes|no)$'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_other_req_text)
        ],
        CONFIRMATION_JOB_POST: [
            CallbackQueryHandler(confirm_job_post, pattern='^confirm_job_post'),
            CallbackQueryHandler(cancel_job_post, pattern='^cancel_job_post')
        ]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
    
    application.add_handler(job_post_handler)

# Job REPOST convo handler
    job_repost_handler = ConversationHandler(
    entry_points=[CommandHandler('jobrepost', job_repost)],
    states={
        SELECT_JOB_TO_REPOST: [CallbackQueryHandler(jobrepost_button)],
        CONFIRMATION_JOB_POST: [
            CallbackQueryHandler(confirm_job_repost, pattern='^confirm_job_repost'),
            CallbackQueryHandler(cancel_job_repost, pattern='^cancel_job_repost')
        ]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
    
    application.add_handler(job_repost_handler)
    
# Add Subscription package convo handler
    add_subscription_handler = ConversationHandler(
        entry_points=[CommandHandler('addsubscription', start_add_subscription)],

        states={
            GET_SUB_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_subscription_name)],
            GET_NUM_MONTHS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_num_months)],
            GET_TOKENS_PER_MONTH: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_tokens_per_month)],
            GET_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_price)]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    application.add_handler(add_subscription_handler)

# Delete subscription package convo handler
    delete_sub_handler = ConversationHandler(
        entry_points=[CommandHandler('deletesubscription', list_subscriptions)],
        states={
            DELETE_CONFIRMATION: [
                CallbackQueryHandler(confirm_deletion, pattern=r'^delete_sub|.*$'),
            ],
            DELETE_SUBSCRIPTION: [
                CallbackQueryHandler(delete_subscription, pattern=r'^confirm_delete|.*$'),
                CallbackQueryHandler(delete_subscription, pattern='cancel_delete')
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    application.add_handler(delete_sub_handler)

# Add token package convo handler
    add_package_handler = ConversationHandler(
        entry_points=[CommandHandler('addpackage', add_package)],
        states={
            PACKAGE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, package_name_input)],
            NUMBER_OF_TOKENS: [MessageHandler(filters.TEXT & ~filters.COMMAND, number_of_tokens_input)],
            PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, purchase_amount_input)],
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, description_input)],
            VALIDITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, validity_input)]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    application.add_handler(add_package_handler)

# Delete token package convo handler
    delete_package_handler = ConversationHandler(
    entry_points=[CommandHandler('deletepackage', delete_package)],
    states={
        SELECT_PACKAGE: [CallbackQueryHandler(select_package)],
        CONFIRM_DELETE: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_delete)],
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
    application.add_handler(delete_package_handler)

# Purchasing tokens convo handler
    purchase_subscription_handler = ConversationHandler(
    entry_points=[CommandHandler('purchase_subscription', purchaseSubscription)],
    states={
        SELECTING_SUBSCRIPTION: [CallbackQueryHandler(subscription_selection)],
        SUBSCRIPTION_PHOTO_REQUESTED: [MessageHandler(filters.PHOTO, verifyPayment)]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
    )
    application.add_handler(purchase_subscription_handler)

# Purchasing tokens convo handler
    purchase_tokens_handler = ConversationHandler(
    entry_points=[CommandHandler('purchase_tokens', purchase_tokens)],
    states={
        SELECTING_PACKAGE: [CallbackQueryHandler(package_selection)],
        PHOTO_REQUESTED: [MessageHandler(filters.PHOTO, verifyPayment)]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
    )
    application.add_handler(purchase_tokens_handler)

#Purchasing shortlists convo handler
    purchase_shortlists_handler = ConversationHandler(
    entry_points=[CommandHandler('purchase_shortlists', purchase_shortlists)],
    states={
        CHOOSE_AMOUNT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_amount_choice),
            CallbackQueryHandler(shortlist_cancel, pattern='^shortlist_cancel_purchase$')
        ],
        CONFIRM_PURCHASE: [
            CallbackQueryHandler(confirm_purchase, pattern='^confirm_purchase$'),
            CallbackQueryHandler(shortlist_cancel, pattern='^shortlist_cancel_purchase$')
        ]
    },
    fallbacks=[CallbackQueryHandler(shortlist_cancel, pattern='shortlist_cancel_purchase')],
    allow_reentry=True  # Allows re-entry if the conversation is ended or canceled
    )
    application.add_handler(purchase_shortlists_handler)

# Shortlisting applicants convo handler
    shortlist_handler = ConversationHandler(
        entry_points=[CommandHandler('shortlist', shortlist)],
        states={
            SELECT_JOB: [
                CallbackQueryHandler(select_job, pattern='^\d+$'),  # Job selection
                CallbackQueryHandler(select_job, pattern='^proceed$'),  # Proceed action
                CallbackQueryHandler(select_job, pattern='^cancel$')  # Cancel action
            ],
            SHOW_APPLICANTS: [
                CallbackQueryHandler(shortlist_applicant, pattern='^shortlist\|'),
                CallbackQueryHandler(handle_no_shortlists, pattern='^no_shortlists$'),
                CallbackQueryHandler(done, pattern='^done$')
            ],
            CHOOSE_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_amount_choice),
                CallbackQueryHandler(shortlist_cancel, pattern='^shortlist_cancel_purchase$')
            ],
            CONFIRM_PURCHASE: [
                CallbackQueryHandler(confirm_purchase, pattern='^confirm_purchase$'),
                CallbackQueryHandler(shortlist_cancel, pattern='^shortlist_cancel_purchase$')
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    application.add_handler(shortlist_handler)

    

# Viewing shortlisted applicants convo handler
    view_shortlisted_handler = ConversationHandler(
    entry_points=[CommandHandler('view_shortlisted', view_shortlisted)],
    states={
        VIEW_JOBS: [
                CallbackQueryHandler(view_applicants, pattern='view_applicants_'),
                CallbackQueryHandler(cancel_view_shortlisted, pattern='^cancel_view_shortlisted')]
    },
    fallbacks=[CallbackQueryHandler(cancel_view_shortlisted, pattern='^cancel_view_shortlisted')],
    # allow_reentry=True
)
    application.add_handler(view_shortlisted_handler)

    # CallbackQueryHandlers
    application.add_handler(CallbackQueryHandler(delete_button, pattern='^delete\\|'))
    # application.add_handler(CallbackQueryHandler(register_button, pattern='^(applicant|agency)$'))
    application.add_handler(CallbackQueryHandler(get_admin_acknowledgement, pattern='^(ss_|jp_)(accept|reject)_\d+$'))
    application.add_handler(CallbackQueryHandler(select_applicant_apply, pattern="^ja_\d+_[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"))
    application.add_handler(CallbackQueryHandler(apply_button_handler, pattern='^apply_\d+$'))
    application.add_handler(CallbackQueryHandler(view_button_handler, pattern='^view_(agency|applicant)_(.+)$'))
    application.add_handler(CallbackQueryHandler(post_a_job_button, pattern="post_a_job"))

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
    loop = asyncio.get_event_loop()
    schedule.every().day.at("00:00").do(lambda: asyncio.run_coroutine_threadsafe(daily_checks(application.bot), loop))

    # Create the asyncio task for running the schedule
    schedule_task = loop.create_task(run_schedule())
    
    # Run application and webserver together
    async with application:
        await application.start()
        await webserver.serve()
        await application.stop()

    await schedule_task
    

if __name__ == "__main__":
    asyncio.run(main())