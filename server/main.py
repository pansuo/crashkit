# -*- coding: utf-8
from datetime import datetime, date, timedelta
import time
import logging
import wsgiref.handlers
import os
import string
import urllib
import sets
from random import Random

from google.appengine.dist import use_library
use_library('django', '1.0')

from google.appengine.ext import webapp
from google.appengine.ext.webapp import template
from google.appengine.api import users
from google.appengine.ext import db
from google.appengine.api import memcache
from google.appengine.api.labs import taskqueue
from django.utils import simplejson as json
from models import *
from processor import process_report
from controllers.base import *
from controllers.product import *
from controllers.account import *
from controllers.invites import *
from commons import *
from controllers.user import *
from controllers.admin import *
from controllers.migration import *

class HomeHandler(BaseHandler):
  
  @prolog()
  def get(self):
    self.render_and_finish('site_home.html')

class AccountDashboardHandler(BaseHandler):

  @prolog(fetch = ['account'])
  def get(self):
    products = self.account.products.order('unique_name').fetch(100)
    for product in products:
      access = self.account_access.product_access_for(product)
      product.access = access
    products = filter(lambda p: p.access.is_listing_allowed(), products)
    for product in products:
      new_bugs = product.bugs.filter('ticket =', None).order('-occurrence_count').fetch(7)
      product.more_new_bugs = (len(new_bugs) == 7)
      product.new_bugs = new_bugs[0:6]
      product.new_bug_count = len(product.new_bugs)
    
    self.data.update(tabid='dashboard-tab', account=self.account, products=products)
    self.render_and_finish('account_dashboard.html')

class ObtainClientIdHandler(BaseHandler):

  @prolog(fetch=['account', 'product_nocheck'])
  def get(self):
    client = Client()
    client.product = self.product
    client.cookie = random_string()
    client.put()
    self.send_urlencoded_and_finish(response = 'ok', client_id = client.key().id(), client_cookie = client.cookie)

class CompatObtainClientIdHandler(BaseHandler):

  @prolog(fetch=['compat_account', 'product_nocheck'])
  def get(self):
    client = Client()
    client.product = self.product
    client.cookie = random_string()
    client.put()
    self.send_urlencoded_and_finish(response = 'ok', client_id = client.key().id(), client_cookie = client.cookie)

class BugListHandler(BaseHandler):

  def show_bug_list(self):
    week_count = 4
    
    bugs = self.bugs_query_filter(self.product.bugs).order('-last_occurrence_on').fetch(1000)
    bugs_by_key = index_by_key(bugs)
    
    interesting_stat_keys = []
    for bug in bugs:
      interesting_stat_keys += [BugWeekStat.key_name_for(bug.key(), date_to_week(bug.last_occurrence_on - timedelta(days=7*i))) for i in range(week_count)]
    stats = BugWeekStat.get_by_key_name(interesting_stat_keys)
    stats = group(lambda s: s._bug, filter(lambda s: s is not None, stats))
    stats = dict([ (b, BugWeekStat.sum(ss, previous_week(date_to_week(bugs_by_key[b].last_occurrence_on), offset=week_count), date_to_week(bugs_by_key[b].last_occurrence_on))) for b,ss in stats.items() ])
    
    for bug in bugs:
      bug.stats = stats.get(bug.key())
    bugs = filter(lambda b: b.stats is not None, bugs)
    
    def sort_bugs_of_a_week(week_start, week_end, bugs):
      for bug in bugs:
        bug.is_new_on_last_week = between(bug.created_at.date(), week_start, week_end)
      return sorted(bugs, lambda a,b: -10*signum(a.is_new_on_last_week-b.is_new_on_last_week) -signum(a.stats.count-b.stats.count))
    
    grouped_bugs = group(lambda bug: date_to_week(bug.last_occurrence_on), bugs).items()
    grouped_bugs = sorted(grouped_bugs, lambda a,b: -signum(a[0]-b[0]))
    grouped_bugs = [ (week, week_to_start_date(week), week_to_end_date(week), bugs ) for week, bugs in grouped_bugs ]
    grouped_bugs = [ (week, week_start, week_end, sort_bugs_of_a_week(week_start, week_end, bugs)) for week, week_start, week_end, bugs in grouped_bugs ]
    
    self.data.update(grouped_bugs=grouped_bugs)
    self.render_and_finish('buglist.html')

  @prolog(fetch=['account', 'product'])
  def get(self):
    self.data.update(tabid = 'bugs-tab')
    self.data.update(mass_actions=dict(reopen=False, close=True, ignore=True))
    self.show_bug_list()

  def bugs_filter(self, bugs):
    return filter(lambda b: b.state == BUG_OPEN, bugs)

  def bugs_query_filter(self, bugs):
    return bugs.filter('state', BUG_OPEN)

class ClosedBugListHandler(BugListHandler):

  @prolog(fetch=['account', 'product'])
  def get(self):
    self.data.update(tabid = 'closed-bugs-tab')
    self.data.update(mass_actions=dict(reopen=True, close=False, ignore=False))
    self.show_bug_list()

  def bugs_filter(self, bugs):
    return filter(lambda b: b.state == BUG_CLOSED, bugs)

  def bugs_query_filter(self, bugs):
    return bugs.filter('state', BUG_CLOSED)

class IgnoredBugListHandler(BugListHandler):

  @prolog(fetch=['account', 'product'])
  def get(self):
    self.data.update(tabid = 'ignored-bugs-tab')
    self.data.update(mass_actions=dict(reopen=True, close=False, ignore=False))
    self.show_bug_list()

  def bugs_filter(self, bugs):
    return filter(lambda b: b.state == BUG_IGNORED, bugs)

  def bugs_query_filter(self, bugs):
    return bugs.filter('state', BUG_IGNORED)

class RecentCaseListHandler(BaseHandler):

  @prolog(fetch=['account', 'product'])
  def get(self):
    bugs = self.product.bugs.order('-occurrence_count').fetch(100)
    self.data.update(bugs = bugs)
    self.render_and_finish('buglist.html')
    
SERVER_DETAIL_VARS = ['GATEWAY_INTERFACE', 'SERVER_NAME', 'SERVER_PORT', 'SERVER_SOFTWARE']
ESSENTIAL_REQUEST_DETAILS = ['REQUEST_METHOD', 'CONTENT_TYPE']
REQUEST_DETAIL_VARS = ['HTTP_ACCEPT', 'HTTP_ACCEPT_ENCODING', 'HTTP_ACCEPT_LANGUAGE',
    'HTTP_HOST', 'HTTP_REFERER', 'HTTP_USER_AGENT', 'PATH_INFO', 'REMOTE_ADDR', 'REMOTE_HOST']
IGNORED_VARS = ['hash', 'Apple_PubSub_Socket_Render', 'COMMAND_MODE', 'CONTENT_LENGTH', 'DISPLAY',
    'DJANGO_SETTINGS_MODULE', 'HOME', 'HTTP_CONNECTION', 'HTTP_COOKIE', 'HTTP_ORIGIN', 'LC_CTYPE',
    'LOGNAME', 'MANPATH', 'OLDPWD', 'PATH', 'PWD', 'QUERY_STRING', 'RUN_MAIN', 'SERVER_PROTOCOL',
    'HTTP_CACHE_CONTROL']

class BugHandler(BaseHandler):

  @prolog(fetch=['account', 'product', 'bug'])
  def get(self):
    cases = self.bug.cases.order('-occurrence_count').fetch(100)
    occurrences = Occurrence.all().filter('case IN', cases[:15]).order('-count').fetch(100)
    clients = Client.get([o.client.key() for o in occurrences])
    
    messages = []
    for o in occurrences:
      mm = o.exception_messages
      if not isinstance(mm, list):
        mm = [mm]
      for m in mm:
        messages.append(m)
    messages = list(sets.Set(messages))
    cover_message = None
    for message in messages:
      if cover_message is None or len(message) < len(cover_message):
        cover_message = message
        
    details = {}
    for occurrence in occurrences:
      for k in occurrence.dynamic_properties():
        detail = details.get(k)
        if detail is None: details[k] = detail = Detail(key_name=Detail.key_name_for(k))
        v = getattr(occurrence, k)
        try:
          detail.frequencies[detail.values.index(v)] += 1
        except ValueError:
          detail.values.append(v)
          detail.frequencies.append(1)
          
    # keep it sorted
    for detail in details.itervalues():
      data = sorted(zip(detail.values, detail.frequencies), lambda a,b: 0 if a[1] == b[1] else (1 if a[1] < b[1] else -1))
      detail.values      = [pair[0] for pair in data]
      detail.frequencies = [pair[1] for pair in data]
      
    # group
    (GET_details, POST_details, COOKIE_details, SESSION_details, custom_details, essential_REQUEST_details, REQUEST_details,
      SERVER_details, env_details) = [], [], [], [], [], [], [], [], []
    for k, detail in details.iteritems():
      if k.startswith('data_G_') or k.startswith('data_GET_'):
        GET_details.append(detail)
      elif k.startswith('data_P_') or k.startswith('data_POST_'):
        POST_details.append(detail)
      elif k.startswith('data_C_'):
        COOKIE_details.append(detail)
      elif k.startswith('data_S_'):
        SESSION_details.append(detail)
      elif k.startswith('env_'):
        name = k[4:]
        if name in IGNORED_VARS:
          pass
        elif name in SERVER_DETAIL_VARS:
          SERVER_details.append(detail)
        elif name in REQUEST_DETAIL_VARS:
          REQUEST_details.append(detail)
        elif name in ESSENTIAL_REQUEST_DETAILS:
          essential_REQUEST_details.append(detail)
        else:
          env_details.append(detail)
      else:
        custom_details.append(detail)
        
    cover_case = cases[0]
    for case in cases:
      if len(case.exceptions) < len(cover_case.exceptions):
        cover_case = case
        
    # data_keys contains all columns that differ across occurrences
    data_keys = [] #list(sets.Set(flatten([ [k for k in o.dynamic_properties() if k in common_map and len(common_map[k])>1] for o in occurrences ])))
    data_keys.sort()
    common_keys = [] #list(sets.Set(common_map.keys()) - sets.Set(data_keys))
    # common_keys.sort()
    # env_items = [(k, common_map[k]) for k in common_keys if k.startswith('env_')]
    # common_data_items = [(k, common_map[k]) for k in common_keys if k.startswith('data_')]
      
    self.data.update(tabid = 'bug-tab', bug_id=True,
        cases=cases, cover_case=cover_case,
        occurrences = occurrences, data_keys = data_keys, cover_message=cover_message,
        GET_details=GET_details, POST_details=POST_details,
        COOKIE_details=COOKIE_details, SESSION_details=SESSION_details,
        custom_details=custom_details,
        essential_REQUEST_details=essential_REQUEST_details, REQUEST_details=REQUEST_details,
        SERVER_details=SERVER_details, env_details=env_details)
    self.render_and_finish('bug.html')

class AssignTicketToBugHandler(BaseHandler):
  
  @prolog(fetch=['account', 'product', 'bug'], check=['is_product_write_allowed'])
  def post(self):
    ticket_name = self.request.get('ticket')
    if ticket_name == None or len(ticket_name.strip()) == 0:
      ticket = None
    else:
      ticket = Ticket.get_or_insert(key_name=Ticket.key_name_for(self.product.key().id_or_name(), ticket_name),
          product=self.product, name=ticket_name)
      
    def txn(bug_key):
      b = Bug.get(bug_key)
      b.ticket = ticket
      b.put()
    db.run_in_transaction(txn, self.bug.key())
    
    self.redirect_and_finish(".")

class ChangeBugStateHandler(BaseHandler):
  
  @prolog(fetch=['account', 'product', 'bug'], check=['is_product_write_allowed'])
  def post(self):
    if self.request.get('open'):
      new_state = 'reopen'
    elif self.request.get('close'):
      new_state = 'close'
    elif self.request.get('ignore'):
      new_state = 'ignore'
      
    def txn(bug_key=self.bug.key()):
      b = Bug.get(bug_key)
      if getattr(b, new_state)():
        b.put()
    db.run_in_transaction(txn, self.bug.key())
    
    self.redirect_and_finish(".")

class MassBugStateEditHandler(BaseHandler):
  
  @prolog(fetch=['account', 'product'], check=['is_product_write_allowed'])
  def post(self):
    action = self.request.get('action')
    key_names = map(lambda s: s.strip(), self.request.get('bugs').strip().split("\n"))
    
    logging.info(repr(key_names))
    
    if action in ['reopen', 'close', 'ignore']:
      new_state = action
    else:
      self.error(400)
      self.response.out.write("Invalid action: '%s'" % action)
      return

    bugs = Bug.get_by_key_name(key_names)
    bugs = filter(lambda b: b is not None, bugs)
    dirty_bugs = []
    for bug in bugs:
      if bug._product != self.product.key():
        self.error(400)
        self.response.out.write("Invalid reference to a bug from another product: '%s'" % bug.key().name())
        return
      if getattr(bug, new_state)():
        dirty_bugs.append(bug)
    db.put(dirty_bugs)
    
    self.redirect_and_finish(".")

class CompatPostBugReportHandler(BaseHandler):

  @prolog(fetch=['compat_account', 'product_nocheck', 'client', 'client_cookie'])
  def post(self):
    body = (self.request.body or '').strip()
    if len(body) == 0:
      self.blow(400, 'json-payload-required')
    
    report = Report(product=self.product, client=self.client, remote_ip=self.request.remote_addr,
        data=unicode(self.request.body, 'utf-8'))
    report.put()
    
    process_report(report)
      
    self.send_urlencoded_and_finish(response = 'ok', status = report.status, error = (report.error or 'none'))
  get=post

class PostBugReportHandler(BaseHandler):

  @prolog(fetch=['account_nocheck', 'product_nocheck', 'client', 'client_cookie'])
  def post(self):
    body = unicode((self.request.body or ''), 'utf-8').strip()
    if len(body) == 0:
      self.blow(400, 'json-payload-required')
    
    report = Report(product=self.product, client=self.client, remote_ip=self.request.remote_addr,
        data=body)
    report.put()
    
    all_blobs = re.findall('"blob:([a-zA-Z0-9]+)"', body)
    existing_blobs = Attachment.get_by_key_name([Attachment.key_name_for(self.product.key(), b) for b in all_blobs])
    blobs = sets.Set(all_blobs) - sets.Set([b.body_hash for b in existing_blobs if b])
    
    taskqueue.add(url = '/qworkers/process-report', params = {'key': report.key().id()})
      
    self.send_urlencoded_and_finish(response = 'ok', status = report.status,
        error = (report.error or 'none'), blobs=','.join(blobs))
  get=post

  def handle_exception(self, exception, debug_mode):
    return webapp.RequestHandler.handle_exception(self, exception, debug_mode)
    
class ProcessReportHandler(BaseHandler):
  def post(self):
    report = Report.get_by_id(int(self.request.get('key')))
    if report.status != REPORT_NEW:
      return
    process_report(report)

class PostBlobHandler(BaseHandler):

  @prolog(fetch=['account_nocheck', 'product_nocheck', 'client', 'client_cookie'])
  def post(self, body_hash):
    body = (unicode(self.request.body, 'utf-8') or u'')
    def txn():
      k = Attachment.key_name_for(self.product.key(), body_hash)
      a = Attachment.get_by_key_name(k)
      if not a:
        a = Attachment(key_name=k, product=self.product, client=self.client, body=body,
            body_hash=body_hash)
        a.put()
    db.run_in_transaction(txn)
  
    self.send_urlencoded_and_finish(response='ok')
  get=post

class ViewBlobHandler(BaseHandler):

  @prolog(fetch=['account', 'product'])
  def get(self, body_hash):
    k = Attachment.key_name_for(self.product.key(), body_hash)
    self.attachment = Attachment.get_by_key_name(k)
    if self.attachment is None:
      self.response.out.write("not found")
    else:
      self.response.headers['Content-Type'] = "text/plain"
      self.response.out.write(self.attachment.body)

url_mapping = [
  ('/', HomeHandler),
  ('/signup/([a-zA-Z0-9]*)', SignupHandler),
  ('/betasignup/', SignUpForLimitedBetaHandler),
  ('/admin/', AdminHandler),
  ('/admin/beta/', LimitedBetaCandidateListHandler),
  ('/admin/beta/accept', LimitedBetaAcceptCandidateHandler),
  ('/admin/beta/reject', LimitedBetaRejectCandidateHandler),
  ('/admin/fuckup/', AdminFuckUpHandler),
  ('/qworkers/process-report', ProcessReportHandler),
  ('/admin/migrate/', MigrateHandler),
  ('/admin/migrate/worker', MigrateWorkerHandler),
  ('/iterate', Temp),
  ('/profile/', ProfileHandler),
  # per-account
  ('/([a-zA-Z0-9._-]+)/', AccountDashboardHandler),
  ('/([a-zA-Z0-9._-]+)/people/', AccountPeopleHandler),
  ('/([a-zA-Z0-9._-]+)/settings/', AccountSettingsHandler),
  # per-project API (compatibility)
  ('/([a-zA-Z0-9._-]+)/obtain-client-id', CompatObtainClientIdHandler),
  ('/([a-zA-Z0-9._-]+)/post-report/([0-9]+)/([a-zA-Z0-9]+)', CompatPostBugReportHandler),
  # per-project API
  ('/([a-zA-Z0-9._-]+)/products/([a-zA-Z0-9._-]+)/obtain-client-id', ObtainClientIdHandler),
  ('/([a-zA-Z0-9._-]+)/products/([a-zA-Z0-9._-]+)/help/integration', ProductHelpHandler),
  ('/([a-zA-Z0-9._-]+)/products/([a-zA-Z0-9._-]+)/post-report/([0-9]+)/([a-zA-Z0-9]+)', PostBugReportHandler),
  ('/([a-zA-Z0-9._-]+)/products/([a-zA-Z0-9._-]+)/post-blob/([0-9]+)/([a-zA-Z0-9]+)/([a-zA-Z0-9]+)', PostBlobHandler),
  # per-project (users)
  ('/([a-zA-Z0-9._-]+)/products/(new)/', ProductSettingsHandler),
  ('/([a-zA-Z0-9._-]+)/products/([a-zA-Z0-9._-]+)/', BugListHandler),
  ('/([a-zA-Z0-9._-]+)/products/([a-zA-Z0-9._-]+)/closed', ClosedBugListHandler),
  ('/([a-zA-Z0-9._-]+)/products/([a-zA-Z0-9._-]+)/ignored', IgnoredBugListHandler),
  ('/([a-zA-Z0-9._-]+)/products/([a-zA-Z0-9._-]+)/settings', ProductSettingsHandler),
  ('/([a-zA-Z0-9._-]+)/products/([a-zA-Z0-9._-]+)/mass-state-edit', MassBugStateEditHandler),
  ('/([a-zA-Z0-9._-]+)/products/([a-zA-Z0-9._-]+)/bugs/([a-zA-Z0-9._-]+)/', BugHandler),
  ('/([a-zA-Z0-9._-]+)/products/([a-zA-Z0-9._-]+)/bugs/([a-zA-Z0-9._-]+)/assign-ticket', AssignTicketToBugHandler),
  ('/([a-zA-Z0-9._-]+)/products/([a-zA-Z0-9._-]+)/bugs/([a-zA-Z0-9._-]+)/change-state', ChangeBugStateHandler),
  ('/([a-zA-Z0-9._-]+)/products/([a-zA-Z0-9._-]+)/blob/([a-zA-Z0-9]+)/', ViewBlobHandler),
]

def main():
  application = webapp.WSGIApplication(url_mapping,
                                       debug=True)
  wsgiref.handlers.CGIHandler().run(application)


if __name__ == '__main__':
  main()
