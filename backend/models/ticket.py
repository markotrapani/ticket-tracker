from datetime import datetime, date
from .database import db


class Ticket(db.Model):
    __tablename__ = 'tickets'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    zendesk_id = db.Column(db.String(10), unique=True, nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False)
    group_name = db.Column(db.String(50), default='Support - L3')
    assignee = db.Column(db.String(100), default='Marko Trapani')
    created_date = db.Column(db.Date, nullable=False)
    solved_date = db.Column(db.Date, nullable=True)

    # Enrichment fields
    subject = db.Column(db.String(500), nullable=True)
    customer_name = db.Column(db.String(200), nullable=True)
    description = db.Column(db.Text, nullable=True)
    root_cause = db.Column(db.Text, nullable=True)
    resolution = db.Column(db.Text, nullable=True)
    zendesk_url = db.Column(db.String(300), nullable=True)

    # Classification
    category = db.Column(db.String(100), nullable=True)
    product_area = db.Column(db.String(100), nullable=True)
    severity = db.Column(db.String(10), nullable=True)
    is_production_outage = db.Column(db.Boolean, default=False)
    is_escalation = db.Column(db.Boolean, default=False)
    involved_custom_scripts = db.Column(db.Boolean, default=False)

    # Enrichment tracking
    enrichment_level = db.Column(db.String(20), default='metadata_only')

    # Significance scoring
    auto_score = db.Column(db.Float, nullable=True)
    content_score = db.Column(db.Float, nullable=True)
    manual_score = db.Column(db.Float, nullable=True)
    final_score = db.Column(db.Float, nullable=True)

    # Interview prep
    is_starred = db.Column(db.Boolean, default=False)
    interview_notes = db.Column(db.Text, nullable=True)
    skills_demonstrated = db.Column(db.Text, nullable=True)  # JSON array

    # Timestamps
    imported_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    tags = db.relationship('TicketTag', backref='ticket', cascade='all, delete-orphan', lazy='dynamic')
    notes = db.relationship('TicketNote', backref='ticket', cascade='all, delete-orphan',
                            lazy='dynamic', order_by='TicketNote.created_at.desc()')
    score_history = db.relationship('ScoreHistory', backref='ticket', cascade='all, delete-orphan', lazy='dynamic')

    @property
    def resolution_days(self):
        if self.solved_date and self.created_date:
            return (self.solved_date - self.created_date).days
        return None

    @property
    def tag_list(self):
        return [t.tag for t in self.tags]

    def to_dict(self):
        return {
            'id': self.id,
            'zendesk_id': self.zendesk_id,
            'status': self.status,
            'group_name': self.group_name,
            'assignee': self.assignee,
            'created_date': self.created_date.isoformat() if self.created_date else None,
            'solved_date': self.solved_date.isoformat() if self.solved_date else None,
            'resolution_days': self.resolution_days,
            'subject': self.subject,
            'customer_name': self.customer_name,
            'description': self.description,
            'root_cause': self.root_cause,
            'resolution': self.resolution,
            'zendesk_url': self.zendesk_url,
            'category': self.category,
            'product_area': self.product_area,
            'severity': self.severity,
            'is_production_outage': self.is_production_outage,
            'is_escalation': self.is_escalation,
            'involved_custom_scripts': self.involved_custom_scripts,
            'enrichment_level': self.enrichment_level,
            'auto_score': self.auto_score,
            'content_score': self.content_score,
            'manual_score': self.manual_score,
            'final_score': self.final_score,
            'is_starred': self.is_starred,
            'interview_notes': self.interview_notes,
            'skills_demonstrated': self.skills_demonstrated,
            'tags': self.tag_list,
            'imported_at': self.imported_at.isoformat() if self.imported_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class TicketTag(db.Model):
    __tablename__ = 'ticket_tags'

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('tickets.id'), nullable=False)
    tag = db.Column(db.String(100), nullable=False, index=True)
    source = db.Column(db.String(20), default='manual')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('ticket_id', 'tag'),)


class TicketNote(db.Model):
    __tablename__ = 'ticket_notes'

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('tickets.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    note_type = db.Column(db.String(20), default='general')
    author = db.Column(db.String(200))
    is_internal = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'ticket_id': self.ticket_id,
            'content': self.content,
            'note_type': self.note_type,
            'author': self.author,
            'is_internal': self.is_internal,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class ScoreHistory(db.Model):
    __tablename__ = 'score_history'

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('tickets.id'), nullable=False)
    score_type = db.Column(db.String(20), nullable=False)
    score_value = db.Column(db.Float, nullable=False)
    components_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
