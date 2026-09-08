from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import login_required
from flask_wtf import FlaskForm
from wtforms import DateField, HiddenField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, Regexp

from app.services import campaign_service
from app.services.auth_service import admin_required

bp = Blueprint("campaigns", __name__)


class CampaignActionForm(FlaskForm):
    expected_fingerprint = HiddenField(validators=[DataRequired(), Length(min=64, max=64)])


class CampaignForm(FlaskForm):
    campaign_id = StringField(
        "Campaign ID",
        validators=[
            DataRequired(),
            Length(max=100),
            Regexp(
                r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
                message="Use letters, numbers, underscores, or hyphens.",
            ),
        ],
    )
    campaign_name = StringField("Campaign name", validators=[DataRequired(), Length(max=200)])
    folder_name = StringField("Drive folder name", validators=[DataRequired(), Length(max=200)])
    campaign_type = StringField("Type", validators=[DataRequired(), Length(max=100)])
    status = SelectField(
        "Initial status", choices=[("INACTIVE", "Inactive"), ("ACTIVE", "Active")]
    )
    start_date = DateField("Start date", validators=[DataRequired()])
    end_date = DateField("End date", validators=[DataRequired()])
    submit = SubmitField("Add campaign")


@bp.get("/campaigns")
@login_required
def active_campaigns():
    try:
        campaigns = campaign_service.list_campaigns(active_only=True)
        return render_template("campaigns/list.html", campaigns=campaigns, admin=False)
    except campaign_service.CampaignError as exc:
        return render_template(
            "campaigns/list.html", campaigns=[], admin=False, error=str(exc)
        ), 503


@bp.get("/admin/campaigns")
@admin_required
def manage_campaigns():
    try:
        campaigns = campaign_service.list_campaigns()
        return render_template(
            "campaigns/list.html", campaigns=campaigns, admin=True, form=CampaignActionForm()
        )
    except campaign_service.CampaignError as exc:
        return render_template(
            "campaigns/list.html",
            campaigns=[],
            admin=True,
            form=CampaignActionForm(),
            error=str(exc),
        ), 503


@bp.route("/admin/campaigns/new", methods=["GET", "POST"])
@admin_required
def create_campaign():
    from flask_login import current_user

    form = CampaignForm()
    if form.validate_on_submit():
        try:
            campaign_service.add_campaign(
                campaign_id=form.campaign_id.data,
                campaign_name=form.campaign_name.data,
                folder_name=form.folder_name.data,
                campaign_type=form.campaign_type.data,
                status=form.status.data,
                start_date=form.start_date.data,
                end_date=form.end_date.data,
                actor=current_user,
            )
            flash("Campaign added to the registry Sheet.", "success")
            return redirect(url_for("campaigns.manage_campaigns"))
        except campaign_service.CampaignError as exc:
            flash(str(exc), "error")
    return render_template("campaigns/form.html", form=form)


@bp.post("/admin/campaigns/<campaign_id>/<action>")
@admin_required
def campaign_action(campaign_id, action):
    from flask_login import current_user

    if action not in ("activate", "deactivate"):
        abort(404)
    form = CampaignActionForm()
    if not form.validate_on_submit():
        abort(400)
    try:
        campaign_service.set_status(
            campaign_id,
            "ACTIVE" if action == "activate" else "INACTIVE",
            form.expected_fingerprint.data,
            current_user,
        )
        flash("Campaign updated in the registry.", "success")
    except campaign_service.CampaignError as exc:
        flash(str(exc), "error")
    return redirect(url_for("campaigns.manage_campaigns"))
