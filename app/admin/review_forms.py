from flask_wtf import FlaskForm
from wtforms import HiddenField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional


class ReviewActionForm(FlaskForm):
    note = TextAreaField("Review note", validators=[Optional(), Length(max=4000)])
    platform = HiddenField()
    submit = SubmitField()


class RequiredNoteForm(FlaskForm):
    note = TextAreaField(
        "Reason or requested changes", validators=[DataRequired(), Length(max=4000)]
    )
    platform = HiddenField()
    submit = SubmitField()
