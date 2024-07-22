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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, InputFile
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

#TODO change/sanitize f-string SQL entries to protect from injection attacks - CHANGE TO SAFE FORMAT SO THAT IT WORKS!!!
#TODO lock all commands in private chats (should not be able to work in group chats)
#TODO add try and except blocks for conversation handlers
#TODO change all
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
CHANNEL_ID = -1002192841091 #TODO change
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
    

async def async_test_db(): #TODO remove
    user_handle = "brandonlmk"
    query_string = f"SELECT id, agency_name FROM agencies WHERE user_handle = '{user_handle}'"
    query_string = "SELECT id, agency_name FROM agencies WHERE user_handle = :user_handle"
    agency_profiles = await safe_get_db(query_string, params={})
    logger.info(agency_profiles) #
    return agency_profiles

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

    keyboard = [
        [InlineKeyboardButton("Singaporean", callback_data='Singaporean')],
        [InlineKeyboardButton("Permenant Resident(PR)", callback_data='Permenant Resident(PR)')],
        [InlineKeyboardButton("Foreigner", callback_data='Foreigner')]
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
        f"INSERT INTO applicants (user_handle, chat_id, name, dob, past_exp, citizenship, race, gender, education, whatsapp_no) VALUES ('{context.user_data['user_handle']}','{context.user_data['chat_id']}', '{context.user_data['name']}', '{context.user_data['dob']}', '{context.user_data['past_experiences']}', '{context.user_data['citizenship']}', '{context.user_data['race']}', '{context.user_data['gender']}', '{context.user_data['highest_education']}', '{context.user_data['whatsapp_number']}')"

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
            job_id = await save_jobpost(context.user_data)
            
            message = await draft_job_post_message(job_id)
            # Send to admin for approval
            return await forward_to_admin_for_acknowledgement(update, context, message=message, job_post_id=job_id)
            # Post job listing in channel
            await post_job_in_channel(update, context, job_post_id=job_id)

    return ENTER_JOB_DETAILS

async def draft_job_post_message(job_id) -> str:
    """    
    Generates job post id to be approved by admin and posted in channel later on.
    Args:
        job_id (_type_): Job Post ID, returned by save_jobpost

    Returns:
        str: Message to be approved by admin
    """    
    # Fetch job details from db
    query_string = f"SELECT agency_id, job_title, company_industry, date_time, pay_rate, job_scope FROM job_posts WHERE id = '{job_id}'"
    results = await get_db(query_string)
    agency_id, job_title, company_industry, date_time, pay_rate, job_scope = results[0]
    # Fetch agency details from agency_id
    query_string = f"SELECT user_handle, chat_id, name, agency_name, agency_uen FROM agencies WHERE id = '{agency_id}'"
    results = await get_db(query_string)
    user_handle, chat_id, name, agency_name, agency_uen = results[0]
    # Draft message template
    message = f'''
    <b><u>Example Job Post [ID: {job_id}]</u></b> (Template to be changed)\n\n
<b>👨🏻‍💻 Agency</b>:\n{agency_name}\n\n
<b>🏢Industry</b>:\n{company_industry}\n\n
<b>👨‍💼Job Title</b>:\n{job_title}\n\n
<b>🤑Salary</b>:\n{pay_rate}\n\n
<b>😓Job Scope</b>:\n{job_scope}\n\n
    '''
    return message


async def post_job_in_channel(update: Update, context: ContextTypes.DEFAULT_TYPE, message, job_post_id): #TODO fix template for job posts, what info should be shown to everyone (company name, etc.)
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
    for applicant_id, applicant_name in applicant_profiles:
        logger.info(applicant_id)
        logger.info(applicant_name)
        keyboard.append([InlineKeyboardButton(f"Applicant - {applicant_name}", callback_data=f"ja_{job_post_id}_{applicant_id}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id=chat_id, text=f"You are appplying for Job ID {job_post_id}\n\nSelect the applicant profile you want to apply with:", reply_markup=reply_markup)

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
    # Add applicant id to job application list #TODO need check if user has already applied with this profile
    query_string = "INSERT INTO job_applications (applicant_id, job_id, shortlist_status) VALUES (:applicant_id, :job_post_id, 'no')"
    params = {"applicant_id": applicant_id, "job_post_id": job_post_id}
    if await safe_set_db(query_string, params):
        logger.info("Inserted applicant into job post list")
    await query.edit_message_text(text=f"{applicant_name} has successfully applied for Job {job_post_id}!") #TODO might need to add applicant_name to message



async def save_jobpost(user_data): #TODO add status
    '''
    Saves job in DB and returns ID of new entry
    '''
    logger.info(f"QUERY: INSERT INTO job_posts (agency_id, job_title, company_industry, date_time, pay_rate, job_scope, shortlist) VALUES ('{user_data['agency_id']}', '{user_data['jobpost_job_title']}', '{user_data['jobpost_company_industry']}', '{user_data['jobpost_date_time']}', '{user_data['jobpost_pay_rate']}', '{user_data['jobpost_job_scope']}', '0')"
)
    async with AsyncSessionLocal() as conn:
        result = await conn.execute(
            sqlalchemy.text(
        f"INSERT INTO job_posts (agency_id, job_title, company_industry, date_time, pay_rate, job_scope, shortlist) VALUES ('{user_data['agency_id']}', '{user_data['jobpost_job_title']}', '{user_data['jobpost_company_industry']}', '{user_data['jobpost_date_time']}', '{user_data['jobpost_pay_rate']}', '{user_data['jobpost_job_scope']}', '0')"
    )
        )
        await conn.commit()
        result = await conn.execute(sqlalchemy.text("SELECT LAST_INSERT_ID()"))
        job_id = result.scalar_one()
        return job_id

###########################################################################################################################################################   
# Purchasing shortlists


###########################################################################################################################################################   
# Shortlisting function

SELECT_JOB, SHOW_APPLICANTS, DONE = range(3)

# Function to start the shortlisting process
async def shortlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Entered shortlist function")
    chat_id = update.effective_chat.id
    
    # Retrieve agency_id(s) for this chat_id from the agencies table
    query = "SELECT id FROM agencies WHERE chat_id = :chat_id"
    agency_ids = await safe_get_db(query, {"chat_id": chat_id})
    
    if not agency_ids:
        await update.message.reply_text("You do not have an agency profile! Type /register to create one.")
        return ConversationHandler.END

    agency_ids = [row[0] for row in agency_ids]

    # Retrieve job titles and industries for the agency_id(s) from the job_posts table
    query = "SELECT id, job_title, company_industry FROM job_posts WHERE agency_id IN :agency_ids"
    job_posts = await safe_get_db(query, {"agency_ids": tuple(agency_ids)})

    if not job_posts:
        await update.message.reply_text("No job posts found for your agencies. To post a job, type /jobpost")
        return ConversationHandler.END

    keyboard = [[InlineKeyboardButton(f"{job[1]} - {job[2]}", callback_data=str(job[0]))] for job in job_posts]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text("Select a job to shortlist applicants for:", reply_markup=reply_markup)
    return SELECT_JOB

# Function to handle job selection
async def select_job(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Entered select job function")
    callback_query = update.callback_query
    await callback_query.answer()
    job_id = int(callback_query.data)
    context.user_data['selected_job_id'] = job_id

    # Retrieve the selected job title and company industry
    query = "SELECT job_title, company_industry FROM job_posts WHERE id = :job_id"
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
    query = "SELECT applicant_id FROM job_applications WHERE job_id = :job_id"
    applicant_ids = await safe_get_db(query, {"job_id": job_id})

    if not applicant_ids:
        await callback_query.message.reply_text("No applicants found for the selected job. Please try again later.")
        return ConversationHandler.END

    applicant_ids = [row[0] for row in applicant_ids]
    context.user_data['remaining_applicants'] = applicant_ids #keep track to end convohandler once all applicants are shortlisted


    # Retrieve applicant details for each applicant_id
    for i, applicant_id in enumerate(applicant_ids, start=1):
        query = (
            "SELECT dob, past_exp, citizenship, race, gender, education "
            "FROM applicants WHERE id = :applicant_id"
        )
        applicant = await safe_get_db(query, {"applicant_id": applicant_id})
        if applicant:
            dob, past_exp, citizenship, race, gender, education = applicant[0]
            applicant_details = (
                f"<b>Applicant {i}</b>\n\n"
                f"<b>DOB:</b> {dob}\n"
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
            reply_markup = InlineKeyboardMarkup(keyboard)
            await callback_query.message.reply_text(applicant_details, reply_markup=reply_markup, parse_mode='HTML')

    return SHOW_APPLICANTS

#
# Function to handle applicant shortlisting
async def shortlist_applicant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Entered shortlist_applicant function")
    
    callback_query = update.callback_query
    await callback_query.answer()  # Acknowledge the callback query

    # Extract applicant_id from callback data
    _, applicant_id = callback_query.data.split('|')
    job_id = context.user_data.get('selected_job_id')

    if not job_id:
        await callback_query.message.reply_text("No job selected. Please select a job first.")
        return SHOW_APPLICANTS  # Continue in the SHOW_APPLICANTS state

    # Update the shortlist status for the selected applicant and job
    logger.info("Updating database")
    query = "UPDATE job_applications SET shortlist_status = 'yes' WHERE job_id = :job_id AND applicant_id = :applicant_id"
    await safe_set_db(query, {"job_id": job_id, "applicant_id": applicant_id})

    # Remove the applicant from the list of remaining applicants
    remaining_applicants = context.user_data.get('remaining_applicants', [])
    remaining_applicants.remove(applicant_id)
    context.user_data['remaining_applicants'] = remaining_applicants

    # Check if there are any remaining applicants to shortlist
    if remaining_applicants:
        # Provide feedback that the applicant has been shortlisted successfully
        await callback_query.message.edit_text("Applicant has been shortlisted successfully!")
        return SHOW_APPLICANTS  # Continue in the SHOW_APPLICANTS state
    else:
        # If no more applicants, end the conversation
        await callback_query.message.edit_text("All applicants have been shortlisted.")
        return ConversationHandler.END


# Function to handle "done" button click
async def done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Entered done function")
    callback_query = update.callback_query
    logger.info(f"CALLBACK QUERY: {callback_query}")
    await callback_query.answer()
    await callback_query.message.reply_text("You have completed the shortlisting process.")
    
    # Optionally, clean up any data or state if needed
    context.user_data.clear()  # Clear user_data if necessary
    
    return ConversationHandler.END


###########################################################################################################################################################   
# Cancel
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
# Add token packages
PACKAGE_NAME, NUMBER_OF_TOKENS, PRICE, DESCRIPTION = range(4)

# Function to handle /addpackage command
#TODO add validity field for admin to fill
async def add_package(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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

# Function to handle the description input and store the package in the database
async def description_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    description = update.message.text
    context.user_data['description'] = description
    print("made it to updating db")
    async with AsyncSessionLocal() as conn:
        await conn.execute(
            sqlalchemy.text(
                "INSERT INTO token_packages (package_name, number_of_tokens, price, description) VALUES (:package_name, :number_of_tokens, :price, :description)"
            ),
            {
                'package_name': context.user_data['package_name'],
                'number_of_tokens': context.user_data['number_of_tokens'],
                'price': context.user_data['price'],
                'description': context.user_data['description']
            }
        )
        await conn.commit()

    print("finished updating db")
    await update.message.reply_text("Token package added successfully!")

    return ConversationHandler.END

###########################################################################################################################################################   
#Delete tokens

SELECT_PACKAGE, CONFIRM_DELETE = range(2)

# Function to handle /deletepackage command
#TODO remove constraints in DB
async def delete_package(update: Update, context: CallbackContext) -> int:
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

async def purchasetokens(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
        #TODO add validity to package_info
        package_info += f"<b>{package_name}</b>:\n{description}\n\n"
        keyboard.append([InlineKeyboardButton(package_name, callback_data=f"select_package|{package_id}")])

    # Check if there are no packages available
    if not token_packages:
        await update.message.reply_text('No token packages available.')
        return ConversationHandler.END

    # Add static package info
    package_info += "<b><u>Token Usage</u></b>\n\n"
    package_info += "<b>3 additional shortlist = </b> 5 tokens\n<b>1 post (+3 shortlist) =</b> 45 tokens \n<b>Repost posting (+3 shortlist) =</b> 30 tokens \n\nPlease select a package:"
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(package_info, reply_markup=reply_markup, parse_mode='HTML')

    return SELECTING_PACKAGE

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
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=message,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
        await update.message.reply_text(
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
    if query_data.startswith('ss_'): 
        logger.info("SS query found")
        # Getting admin response as well as transaction ID
        status, transaction_id = query_data.split('_')[1:]
        # Select chat_id based on transaction_id
        query_string = f"SELECT chat_id, package_id FROM transactions WHERE transaction_id = '{transaction_id}'"
        results = await get_db(query_string)
        logger.info(f"Results: {results}")
        chat_id, package_id = results[0]
        # # Get chat id from agency id
        # query_string = f"SELECT chat_id FROM agencies WHERE id = '{agency_id}'"
        # results = await get_db(query_string)
        # chat_id = results[0][0]

        if status == 'accept':
            # Update transaction entry status to 'Approved'
            query_string = f"UPDATE transactions SET status = 'Approved' WHERE transaction_id = '{transaction_id}'"
            await set_db(query_string)
            logger.info(f"Approved {transaction_id} in database!")
            # Update balance of user account
            (new_balance, exp_date) = await update_balance(chat_id=chat_id, package_id=package_id)
            exp_date = exp_date.date()
            await query.answer()  # Acknowledge the callback query to remove the loading state

            
            # Edit the caption of the photo message
            await query.edit_message_caption(caption="You have acknowledged the screenshot.\n\nCredits have been transferred.")
            
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
            await context.bot.send_message(chat_id=chat_id, text="Your payment has been rejected by an admin. Please PM admin for more details")

    elif query_data.startswith('jp_'): 
        logger.info("JP query found")
        # Getting admin response as well as job ID
        status, job_post_id = query_data.split('_')[1:]
        # Get agency id based on job id
        query_string = f"SELECT agency_id FROM job_posts WHERE id = '{job_post_id}'"
        results = await get_db(query_string)
        agency_id = results[0][0]
        # Get chat id from agency id 
        query_string = f"SELECT chat_id FROM agencies WHERE id = '{agency_id}'"
        results = await get_db(query_string)
        chat_id = results[0][0]


        if status == 'accept':
            # Update job post status to 'Approved'
            query_string = f"UPDATE job_posts SET status = 'Approved' WHERE id = '{job_post_id}'"
            await set_db(query_string)
            logger.info(f"Approved {job_post_id} in database!")
            # Post to channel
            message = await draft_job_post_message(job_post_id)
            await post_job_in_channel(update, context, message=message, job_post_id=job_post_id)
            # Alert user of approval
            await query.answer()  # Acknowledge the callback query to remove the loading state
            
            # Edit the caption of the photo message
            await query.edit_message_text(text="You have approved this Job Posting.\n\nAgency will be notifed.")
            
            # Notify the user
            await context.bot.send_message(chat_id=chat_id, text=f"Your posting has been approved by the admin!.\n\nIt has been posted in the channel with Job ID: {job_post_id}")
        
        elif status == 'reject':
            # Update job post status to 'Rejected'
            query_string = f"UPDATE job_posts SET status = 'Rejected' WHERE id = '{job_post_id}'"
            await set_db(query_string)
            logger.info(f"Rejected {job_post_id} in database!")
            await query.answer()  # Acknowledge the callback query to remove the loading state
            
            # Edit the caption of the photo message
            await query.edit_message_text(text="You have rejected the Job Posting.\n\nAgency will be notified")
            
            # Notify the user
            await context.bot.send_message(chat_id=chat_id, text="Your posting has been rejected by an admin. Please PM admin for more details")






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
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"An error occurred: {context.error}")

# Bot classes

###########################################################################################################################################################   
# Function to check and update expired credits
async def remove_expired_credits(bot):
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
            # notify users that their credits have expired
            await bot.send_message(chat_id=chat_id, text=f"{expiring_tokens} tokens have expired today!\n\nTo purchase more tokens, please use the /purchasetokens command!")
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

    application.add_handler(CommandHandler('get_chat_id', get_chat_id))
    # application.add_handler(CommandHandler('send_message_to_group', send_message_to_group))



    #TODO remove since combined with purchase_tokens_handler
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
        CITIZENSHIP: [CallbackQueryHandler(citizenship_button)],
        RACE: [CallbackQueryHandler(race_button)],
        GENDER: [CallbackQueryHandler(gender_button)],
        HIGHEST_EDUCATION: [CallbackQueryHandler(highest_education_button)],
        WHATSAPP_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_text_handler)],
        FULL_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_text_handler)],
        COMPANY_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_text_handler)],
        COMPANY_UEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_text_handler)]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
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
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    application.add_handler(edit_profile_handler)

# Job post convo handler
    job_post_handler = ConversationHandler(
    entry_points=[CommandHandler('jobpost', job_post)],
    states={
        SELECT_AGENCY: [CallbackQueryHandler(jobpost_button)],
        ENTER_JOB_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, jobpost_text_handler)],
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
    
    application.add_handler(job_post_handler)
    

#Add token package convo handler
    add_package_handler = ConversationHandler(
    entry_points=[CommandHandler('addpackage', add_package)],
    states={
        PACKAGE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, package_name_input)],
        NUMBER_OF_TOKENS: [MessageHandler(filters.TEXT & ~filters.COMMAND, number_of_tokens_input)],
        PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, purchase_amount_input)],
        DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, description_input)]
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
    purchase_tokens_handler = ConversationHandler(
    entry_points=[CommandHandler('purchasetokens', purchasetokens)],
    states={
        SELECTING_PACKAGE: [CallbackQueryHandler(package_selection)],
        PHOTO_REQUESTED: [MessageHandler(filters.PHOTO, verifyPayment)]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
    )
    application.add_handler(purchase_tokens_handler)


    shortlist_handler = ConversationHandler(
    entry_points=[CommandHandler('shortlist', shortlist)],
    states={
        SELECT_JOB: [CallbackQueryHandler(select_job, pattern='^\\d+$')],
        SHOW_APPLICANTS: [
            CallbackQueryHandler(shortlist_applicant, pattern='^shortlist\\|'),
            CallbackQueryHandler(done, pattern='^done$')  # Ensure pattern matches exactly
        ],
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)

    # Add the handler to the application
    application.add_handler(shortlist_handler)


    # CallbackQueryHandlers
    application.add_handler(CallbackQueryHandler(delete_button, pattern='^delete\\|'))
    application.add_handler(CallbackQueryHandler(register_button, pattern='^(applicant|agency)$'))
    application.add_handler(CallbackQueryHandler(get_admin_acknowledgement, pattern='^(ss_|jp_)(accept|reject)_\d+$')) #TODO change to trasnaction ID pattern
    application.add_handler(CallbackQueryHandler(select_applicant_apply, pattern="^ja_\d+_[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"))
    application.add_handler(CallbackQueryHandler(apply_button_handler, pattern='^apply_\d+$'))
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
    schedule.every().day.at("00:00").do(lambda: asyncio.run_coroutine_threadsafe(remove_expired_credits(application.bot), loop))

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