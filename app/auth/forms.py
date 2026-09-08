from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, Length


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=254)])
    password = PasswordField("Password", validators=[DataRequired(), Length(max=128)])
    submit = SubmitField("Log in")


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField(
        "Current password", validators=[DataRequired(), Length(max=128)]
    )
    password = PasswordField("New password", validators=[DataRequired(), Length(min=12, max=128)])
    confirm = PasswordField(
        "Confirm new password", validators=[DataRequired(), EqualTo("password")]
    )
    submit = SubmitField("Change password")


class ActionForm(FlaskForm):
    pass
