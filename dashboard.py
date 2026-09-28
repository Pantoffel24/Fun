import streamlit as st

st.session_state.page = 'home'

st.sidebar.write('This is the sidebar')
if st.sidebar.button('Home'):
    st.session_state.page = "home"
if st.sidebar.button('Other'):
    st.session_state.page = 'other'

if st.session_state.page == "home":
    with st.container():
        st.write("Home page")
        col1, col2 = st.columns(2)
        with col1:
            st.write("To-Do")
            with st.container(border = True):
                st.write('finish assignment for tuesday')
        with col2:
            st.write('column 2')
            st.container(border = True)


if st.session_state.page == 'other':
    with st.container():
        st.write("Other text")



