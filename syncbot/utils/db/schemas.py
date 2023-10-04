import sqlalchemy
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.types import DECIMAL
from sqlalchemy.orm import relationship

BaseClass = declarative_base(mapper=sqlalchemy.orm.mapper)


class GetDBClass:
    def get_id(self):
        return self.id

    def get(self, attr):
        if attr in [c.key for c in self.__table__.columns]:
            return getattr(self, attr)
        return None

    def to_json(self):
        return {c.key: self.get(c.key) for c in self.__table__.columns}

    def __repr__(self):
        return str(self.to_json())


class Region(BaseClass, GetDBClass):
    __tablename__ = "regions"
    id = Column(Integer, primary_key=True)
    team_id = Column(String(100), unique=True)
    workspace_name = Column(String(100))
    bot_token = Column(String(100))


class Sync(BaseClass, GetDBClass):
    __tablename__ = "syncs"
    id = Column(Integer, primary_key=True)
    title = Column(String(100), unique=True)
    description = Column(String(100))


class SyncChannel(BaseClass, GetDBClass):
    __tablename__ = "sync_channels"
    id = Column(Integer, primary_key=True)
    sync_id = Column(Integer, ForeignKey("syncs.id"))
    region_id = Column(Integer, ForeignKey("regions.id"))
    region = relationship("Region", backref="sync_channels")
    channel_id = Column(String(100))


class PostMeta(BaseClass, GetDBClass):
    __tablename__ = "post_meta"
    id = Column(Integer, primary_key=True)
    post_id = Column(String(100))
    sync_channel_id = Column(Integer, ForeignKey("sync_channels.id"))
    ts = Column(DECIMAL(16, 6))


# class SyncChannelExtended(BaseClass, GetDBClass):
#     __tablename__ = "sync_channels_extended"
#     id = Column(Integer, primary_key=True)
#     sync_id = Column(Integer)
#     region_id = Column(Integer)
#     channel_id = Column(String(100))
#     sync_title = Column(String(100))
#     sync_description = Column(String(100))
#     region_team_id = Column(String(100))
#     region_workspace_name = Column(String(100))
#     region_bot_token = Column(String(100))
