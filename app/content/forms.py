from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, MultipleFileField
from wtforms import DateField, HiddenField, SelectField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional


class SubmissionForm(FlaskForm):
    campaign_id = SelectField("Campaign", validators=[DataRequired()])
    target_date = DateField("Publishing date", validators=[DataRequired()])
    default_caption = TextAreaField("Default caption", validators=[Optional(), Length(max=10000)])
    version = HiddenField(default="0")
    submit = SubmitField("Save draft")


class UploadForm(FlaskForm):
    files = MultipleFileField(
        "Media files",
        validators=[
            DataRequired(),
            FileAllowed(["jpg", "jpeg", "png", "mp4", "pdf"], "Use JPEG, PNG, MP4, or PDF."),
        ],
    )
    media_scope = SelectField("Use media for", choices=[("shared", "All selected platforms")])
    submit = SubmitField("Upload")


class ReorderForm(FlaskForm):
    ordered_ids = HiddenField(validators=[DataRequired(), Length(max=2000)])
    media_scope = HiddenField(default="shared")


class SubmitForm(FlaskForm):
    pass
