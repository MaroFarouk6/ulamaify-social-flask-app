from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, Length


class CreateUserForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=120)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=254)])
    role = SelectField("Role", choices=[("content_creator", "Content creator"), ("admin", "Admin")])
    password = PasswordField(
        "Temporary password", validators=[DataRequired(), Length(min=12, max=128)]
    )
    must_change = BooleanField("Require password change on first login", default=True)
    submit = SubmitField("Create user")


class ResetPasswordForm(FlaskForm):
    password = PasswordField(
        "New temporary password", validators=[DataRequired(), Length(min=12, max=128)]
    )
    submit = SubmitField("Reset password")
