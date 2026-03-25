import streamlit as st

st.set_page_config(page_title='Simple Submit Form')
st.title('Simple Submit Form')
st.write('Use this form to submit your name, email, and a brief message for follow-up.')

with st.form(key='simple_submit_form'):
    name = st.text_input('Name')
    email = st.text_input('Email')
    message = st.text_area('Message')
    submitted = st.form_submit_button('Submit')

if submitted:
    st.success('Form submitted successfully')
