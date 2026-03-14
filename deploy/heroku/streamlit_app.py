"""Simple Streamlit entrypoint for Heroku deployment."""

from __future__ import annotations

import os

import streamlit as st

API_BASE_URL = os.getenv("API_BASE_URL", "")
PAGE_TITLE = os.getenv("STREAMLIT_PAGE_TITLE", "Dynamic Exec Streamlit UI")
PAGE_DESCRIPTION = os.getenv(
    "STREAMLIT_PAGE_DESCRIPTION",
    "This Streamlit app is deployed separately from the Flask API on Heroku.",
)

st.set_page_config(page_title=PAGE_TITLE)
st.title(PAGE_TITLE)
st.write(PAGE_DESCRIPTION)

if API_BASE_URL.strip():
    st.success(f"Flask API URL: {API_BASE_URL.strip()}")
else:
    st.info("Set API_BASE_URL config var to display your Flask API endpoint.")

with st.form(key="heroku_demo_form"):
    name = st.text_input("Name")
    email = st.text_input("Email")
    message = st.text_area("Message")
    submitted = st.form_submit_button("Submit")

if submitted:
    st.success("Form submitted")
    st.json({"name": name, "email": email, "message": message})
