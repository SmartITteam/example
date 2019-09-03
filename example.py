# -*- coding: utf-8 -*-
import json
import re
from datetime import datetime
from urllib.parse import urlencode, urlparse, parse_qs

import scrapy
from bs4 import BeautifulSoup as bs
from scrapy import Request, FormRequest
from medical_scraper.scrap_aws_ses import send_emails
import platform

from medical_scraper.zmq_client import ZMQClient
from .tmhp import Tmhp
import time

html_render_url = 'http://127.0.0.1:9000/htmltopdf'
hostname = platform.node()


def build_url(url, params=dict()):
    return "{}?{}".format(url, urlencode(params))

output_date_format = '%m/%d/%Y'
input_date_format = '%Y-%m-%d'


def convert_date(date):
    try:
        return datetime.strptime(date, input_date_format).strftime(output_date_format)
    except ValueError:
        return ''
    except TypeError:
        return ''


class McnaSpider(scrapy.Spider):
    name = 'mcna'
    allowed_domains = ['mcna.net', 'localhost', '127.0.0.1', 'xxxxx.com']
    # Define the urls used in the scraper
    base_url = 'https://xxxxxxxxxxxxxx.net'
    login_url = base_url + '/login/portal_user_authenticate.json'
    roster_url = base_url + '/provider/members_roster'
    members_url = base_url + '/provider/members_roster_list.json'
    member_info_url = base_url + '/provider/get_member_info.json'
    verify_eligibility_url = base_url + '/provider/verify_eligibility.json'
    member_eligibility_url = base_url + '/provider/eligible/{}/{}/{}/{}/0/1'
    start_urls = [base_url]

    custom_settings = {}

    def __init__(self, creds='', scrape_mode='all', members='', *args, **kwargs):
        super(McnaSpider, self).__init__(*args, **kwargs)
        self.creds = creds
        self.scrape_mode = scrape_mode
        self.creds = json.loads(self.creds) if isinstance(self.creds, str) else self.creds
        self.members = members
        self.counter = 0
        self.zclient = ZMQClient()
        tmhp_username = self.creds[0]['tmhp_username']
        tmhp_password = self.creds[0]['tmhp_password']
        if tmhp_username and not self.scrape_mode == 'validate':
            self.tmhp = Tmhp(logger=self.logger, jobid=self.creds[0]['jobid'], username=tmhp_username,
                             password=tmhp_password, mode=scrape_mode)
        else:
            self.tmhp = None
        if scrape_mode == 'partial':
            self.members = json.loads(self.members) if isinstance(self.members, str) else self.members
            for i, m in enumerate(self.members):
                self.members[i]['dob'] = convert_date(m['dob'])

    def reset_counter(self):
        self.counter = 0

    def repeat_request(self, response, subject, body):
        time_sleep = (5, 10, 15, 30)
        self.counter = 0 if self.counter > 4 else self.counter
        self.counter += 1
        if self.counter <= 4:
            time.sleep(time_sleep[self.counter - 1])
            return response.request.replace(dont_filter=True)
        else:
            send_emails(subject=subject, body=body)

    def error_handler(self, error):
        self.logger.exception(error)
        err = str(error).split('\n')
        send_emails(subject="MCNA response failed", body="On host {} \n MCNA Spider Error: {}".format(hostname, err[0][1:]))

    # Visit homepage for every user read from file with unique cookiejar for separate session of each user
    def start_requests(self):
        try:
            for user in self.creds:
                item = dict()
                item['creds'] = user
                item['jobid'] = user['jobid']
                item['company'] = user['company']
                item['practice'] = user['practice'] or user['practice[]']
                item['facility_id'] = user['facility_id']
                data = Request(self.base_url, callback=self.parse_homepage, errback=self.error_handler)
                data.meta['item'] = item
                data.meta['cookiejar'] = user['username']
                yield data
                if self.tmhp:
                    yield self.tmhp.start_requests()
        except Exception as e:
            msg = 'Error occurred while visit homepage for member with facility ID {} for user "{}", {} company and {}'\
                  ' practice \n Error is: {}'.format(item.get('facility_id'), item.get('username'), item.get('company'),
                                                     item.get('practice'), e)
            send_emails(subject="MCNA homepage failed", body='On host {} \n {}'.format(hostname, msg))
            self.logger.exception(msg)

    def set_status(self, status, item, one_member=False):
        if one_member:
            item['mco_sync_status'] = status
            item['one_member'] = one_member
        else:
            item['mco_sync_status'] = status
            item['members'] = self.members
        return item

    # Get login form token from homepage
    def parse_homepage(self, response):
        try:
            item = response.meta['item'].copy()
            yield self.set_status('Pending', item)
            auth_token = re.search(r'AUTH_TOKEN = "(.*)";', response.text)
            if auth_token:
                fdata = {'username': item['creds']['username'],
                         'password': item['creds']['password'],
                         'authenticity_token': auth_token.group(1)}
                data = FormRequest(self.login_url, formdata=fdata, callback=self.parse_login, errback=self.error_handler)
                data.meta['item'] = item
                data.meta['cookiejar'] = response.meta['cookiejar']
                yield data
            else:
                msg = 'Authentication token not found on homepage for user "{}".'.format(item['username'])
                self.logger.error(msg)
                if self.scrape_mode == 'validate':
                    self.logger.info('Validation Failed')
                    item['result'] = 'Invalid'
                    yield item
        except Exception as e:
            msg = 'Error occurred while get login form token from homepage for member with {} facility ID for {} user' \
                  ', {} company and {} practice \n Error is: {}'.format(
                item.get('facility_id'), item.get('username'), item.get('company'), item.get('practice'), e)
            send_emails(subject="MCNA parse home page failed", body='On host {} \n {}'.format(hostname, msg))
            self.logger.exception(msg)

    # Verify login successful and proceed to fetch facility ids
    def parse_login(self, response):
        try:
            item = response.meta['item'].copy()
            item['username'] = item['creds']['username']
            # Remove creds from the item as not required in later requests
            item.pop('creds', None)
            # Parse login api response
            login_reponse = json.loads(response.text)
            # If login not successful raise error and stop spider else  get facility id
            if login_reponse['portal_user_authenticate']['response_message'] != 'OK':
                yield self.set_status('Outdated', item)
                msg = 'Authentication failed for user "{}". Error captured: {}'.format(
                    item['username'], login_reponse.get('portal_user_authenticate').get('response_message'))
                self.logger.error(msg)
                send_emails(subject="MCNA authentication failed", body='On host {} \n {}'.format(hostname, msg))
                if self.scrape_mode == 'validate':
                    self.logger.info('Validation Failed')
                    item['result'] = 'Invalid'
                    yield item
            else:
                msg = 'Login successful for user "{}"'.format(item['username'])
                self.logger.info(msg)
                # If scraping all then parse facility ids else start getting eligibility info for members
                if self.scrape_mode in ['all', 'validate']:
                    data = Request(self.roster_url, callback=self.parse_facility_id, errback=self.error_handler)
                    data.meta['item'] = item
                    data.meta['cookiejar'] = response.meta['cookiejar']
                    self.reset_counter()
                    yield data
                else:
                    for member in self.members:
                        # Read only members specified for the current logged in user
                        if member['username'] == item['username']:
                            member['practice'] = item['practice']
                            item = member
                            # Save input dob for later use in output
                            item['dob'] = item['Member Date of Birth'] if 'dob' not in item.keys() else item['dob']
                            # Manage cli or web ui input for date of birth field
                            month, day, year = item['Member Date of Birth'].split('/') if 'dob' not in item.keys() else \
                                item[
                                    'dob'].split('/')
                            dob_formatted = "-".join([year, month, day])
                            # Proceed only if valid mid is present in the input data
                            if item['mid'] != '':
                                url = self.member_eligibility_url.format(item['mid'], item['subscriber_id'],
                                                                         dob_formatted,
                                                                         item['fid'])

                                msg = 'Requesting eligibility info for member with mid {} and facility ID {} for user "{}"'.format(
                                    item['mid'], item['fid'], item['username'])
                                self.logger.info(msg)
                                data = Request(url, callback=self.parse_member_eligibility, errback=self.error_handler,
                                               headers={'Referer': 'https://portal.mcna.net/provider/verify_eligibility'})
                                data.meta['item'] = item
                                data.meta['cookiejar'] = response.meta['cookiejar']
                                self.reset_counter()
                                yield data
                            else:
                                query = {'verifyDob': item['dob'],
                                         'verifySubscriberId': item['subscriber_id'],
                                         'verifyLastName': '',
                                         'verifyFirstName': '',
                                         'verifyZip': '',
                                         'providerFacilityId': item['fid']}
                                url = build_url(self.verify_eligibility_url, query)
                                msg = 'Requesting verify eligibility page for member with subscriber id {} and facility ID {} for user "{}"'.format(
                                    item['subscriber_id'], item['fid'], item['username'])
                                self.logger.info(msg)
                                data = Request(url, callback=self.parse_verify_eligibility, errback=self.error_handler,
                                               headers={'X-Requested-With': 'XMLHttpRequest',
                                                        'Referer': 'https://portal.mcna.net/provider/verify_eligibility'})
                                data.meta['item'] = item
                                data.meta['cookiejar'] = response.meta['cookiejar']
                                self.reset_counter()
                                yield data
                                pass
        except Exception as e:
            yield self.set_status('Outdated', item)
            msg = 'Error occurred while Verify login successful and proceed to fetch facility ids for member with ' \
                  'facility ID {} for user "{}", {} company and {} practice \n Error is: {}'.format(
                item.get('facility_id'), item.get('username'), item.get('company'), item.get('practice'), e)
            self.logger.exception(msg)
            yield self.repeat_request(response, "MCNA parse login failed",
                                      'On host {} \n {}'.format(hostname, msg))

    def parse_verify_eligibility(self, response):
        item = response.meta['item'].copy()
        try:
            data = json.loads(response.text)
            eligbility = data['verify_eligibility']
            # if response message is OK then add mid and formatted dob to item and proceed to eligibility page
            if eligbility['response_message'] == 'OK':
                item['mid'] = eligbility['insured']['id']
                month, day, year = item['Member Date of Birth'].split('/') if 'dob' not in item.keys() else item[
                    'dob'].split('/')
                dob_formatted = "-".join([year, month, day])
                url = self.member_eligibility_url.format(item['mid'], item['subscriber_id'], dob_formatted, item['fid'])
                msg = 'Requesting eligibility info for member with mid {} and facility ID {} for user "{}"'.format(
                    item['mid'], item['fid'], item['username'])
                self.logger.info(msg)
                data = Request(url, callback=self.parse_member_eligibility, errback=self.error_handler)
                data.meta['item'] = item
                data.meta['cookiejar'] = response.meta['cookiejar']
                yield data
            else:
                msg = 'Error occurred while parsing verify eligibility data for member with subscriber id {} and ' \
                      'facility ID {} for user "{}"'.format(item['subscriber_id'], item['fid'], item['username'])
                self.logger.error(msg)
                send_emails(subject="MCNA parse verify eligibility failed", body='On host {} \n {}'.format(hostname, msg))
                self.logger.error('Received response: {}'.format(response.text))
        except Exception as e:
            yield self.set_status('Outdated', item)
            msg = 'Error occurred while parsing verify eligibility data for member with subscriber id {} and facility' \
                  ' ID {} for user "{}", {} company and {} practice \n Error is: {}'.format(item.get('subscriber_id'),
                item.get('facility_id'), item.get('username'), item.get('company'), item.get('practice'), e)
            self.logger.exception(msg)
            self.logger.exception(e)
            send_emails(subject="MCNA parse verify eligibility failed", body='On host {} \n {}'.format(hostname, msg))
            self.logger.error('Received response: {}'.format(response.text))

    # Parse facility/facilities user is assigned to and get member list for each
    def parse_facility_id(self, response):
        # Verify the response url before proceeding
        self.logger.debug(response.url)
        if response.url != self.roster_url:
            item = response.meta['item'].copy()
            yield self.set_status('Outdated', item)
            # Something went wrong with login page , authentication failure or some unexpected page
            msg = 'Unexpected page received, manually login and check for any unexpected redirects after login for ' \
                  'member with subscriber id {} and facility ID {} for user "{}", {} company and {} practice'.format(
                item.get('subscriber_id'), item.get('facility_id'), item.get('username'), item.get('company'), item.get('practice'))
            self.logger.error(msg)
            send_emails(subject="MCNA parse facility id failed", body='On host {} \n {}'.format(hostname, msg))
            self.logger.error('Received following page:')
            self.logger.error(response.url)
            self.logger.error('Received response: {}'.format(response.text))
            if self.scrape_mode == 'validate':
                item = response.meta['item'].copy()
                self.logger.info('Validation Failed')
                item['result'] = 'Invalid'
                yield item
        else:
            try:
                # Parse the html page
                soup = bs(response.text, 'html.parser')
                # Find input element with id=facilityId in the page
                fid = soup.find('input', id='facilityId')
                # If non blank facility id found save it in list
                fid_list = []
                # Create a dictionary if in validate mode
                if self.scrape_mode == 'validate':
                    fid_list = dict()
                if fid and fid['value'] != "":
                    if self.scrape_mode == 'validate':
                        fid_list[fid['value']] = ''
                    else:
                        fid_list.append(fid['value'])
                # Else search for facility ID selection box in case of multiple facility assigned for user
                # For some unknown reason BS failing to parse the option of select element hence using a div containing it
                else:
                    facilities_selection = soup.find('div', id='headerText')
                    # Get all member provider IDS
                    facilities = facilities_selection.find_all('option')
                    if facilities:
                        # Store member provider ids in list if not 0 which is 'Select a Facility'
                        for f in facilities:
                            if f['value'] != "0":
                                if self.scrape_mode == 'validate':
                                    fid_list[f['value']] = f.text
                                else:
                                    fid_list.append(f['value'])
                                    # Validate mode
                if self.scrape_mode == 'validate':
                    item = response.meta['item'].copy()
                    self.logger.info('Validation Successful')
                    item['result'] = 'Valid'
                    item['fid_map'] = fid_list
                    yield item
                else:
                    # If no facility  ID found return blank list and print error
                    if len(fid_list) == 0:
                        item = response.meta['item'].copy()
                        msg = 'Facility ID not found for user "{}".'.format(response.meta['item']['username'])
                        self.logger.error(msg)
                        if self.scrape_mode == 'validate':
                            item = response.meta['item'].copy()
                            self.logger.info('Validation Failed')
                            item['result'] = 'Invalid'
                            yield item
                    else:
                        self.logger.debug(fid_list)
                        item = response.meta['item'].copy()
                        # Check if provided facility id at start is valid for this user
                        if item['facility_id'] in fid_list:
                            # We are good to go with the provided facility id
                            item['fid'] = item['facility_id']
                            for i in range(ord('a'), ord('z') + 1):
                                query = {'alpha': chr(i), 'providerFacilityId': item['fid']}
                                url = build_url(self.members_url, query)
                                msg = 'Fetching members for alphabet {} with facility ID {} for user "{}"'. \
                                    format(chr(i), item['fid'], item['username'])
                                self.logger.debug(msg)
                                # Update headers to mimic the data is requested by ajax call
                                data = Request(url, callback=self.parse_members, errback=self.error_handler,
                                               headers={'X-Requested-With': 'XMLHttpRequest'})
                                data.meta['item'] = item
                                data.meta['cookiejar'] = response.meta['cookiejar']
                                self.reset_counter()
                                yield data
                        else:
                            # Else log error and stop crawling process
                            msg = 'Facility ID {} not matching for user "{}".'.format(item['facility_id'],
                                                                                      item['username'])
                            self.logger.error(msg)
                            if self.scrape_mode == 'validate':
                                self.logger.info('Validation Failed')
                                item['result'] = 'Invalid'
                                yield item
            except Exception as e:
                yield self.set_status('Outdated', item)
                msg = 'Error occurred while parsing for member with subscriber id {} and facility ID {} for user "{}"' \
                      ', {} company and {} practice \n Error is: {}'.format(item.get('subscriber_id'),
                item.get('facility_id'), item.get('username'), item.get('company'), item.get('practice'), e)
                send_emails(subject="MCNA parse facility id failed", body='On host {} \n {}'.format(hostname, msg))
                self.logger.exception(msg)
                self.logger.error('Received response: {}'.format(response.text))
                if self.scrape_mode == 'validate':
                    self.logger.info('Validation Failed')
                    item['result'] = 'Invalid'
                    yield item

    # Parse members list received for each alphabet with specified facility ID and get additional info per member
    def parse_members(self, response):
        memberdata = json.loads(response.text)
        # Append member records for current alphabet to main list and print log if no records
        self.logger.debug(memberdata)
        members = []
        try:
            # If single record received then it's dictionary so append
            num_recs = memberdata['members_roster_list']["num_recs"]
            o = urlparse(response.url)
            query = parse_qs(o.query)
            if num_recs == "1":
                records = memberdata['members_roster_list']['members']
                msg = 'Members data received for alphabet {} with facility ID {} for user "{}"'.format(
                    query['alpha'], response.meta['item']['fid'], response.meta['item']['username'])
                members.append(records)
                self.logger.info(msg)
            # If no records just notify
            elif num_recs == "0":
                msg = 'No members data received for alphabet {} with facility ID {} for user "{}"'.format(
                    query['alpha'], response.meta['item']['fid'], response.meta['item']['username'])
                self.logger.info(msg)
            # else it's list so concatenate
            else:
                records = memberdata['members_roster_list']['members']
                members += (records)
                msg = 'Members data received for alphabet {} with facility ID {} for user "{}"'.format(
                    query['alpha'], response.meta['item']['fid'], response.meta['item']['username'])
                self.logger.info(msg)
            # Store member data and request for additional details
            for member in members:
                item = response.meta['item'].copy()
                # Get additional details of the member
                item['fname'] = member['fname']
                item['lname'] = member['lname']
                item['city'] = member['city']
                item['mid'] = member['id']
                if self.scrape_mode == 'validate':
                    item['patient_uuid'] = member['patient_uuid']
                item['dentist'] = '{}, {} {}'.format(member['prov_lname'], member['prov_fname'], member['prov_title'])
                query = {'id': member['id'], 'providerFacilityId': item['fid']}
                url = build_url(self.member_info_url, query)
                data = Request(url, callback=self.parse_member_info, headers={'X-Requested-With': 'XMLHttpRequest'},
                               errback=self.error_handler)
                data.meta['item'] = item
                data.meta['cookiejar'] = response.meta['cookiejar']
                self.reset_counter()
                yield data
        except Exception as e:
            msg = 'Error occurred while parsing members data for member with subscriber id {} and facility' \
                  ' ID {} for user "{}", {} company and {} practice \n Error is: {}'.format(item.get('subscriber_id'),
                item.get('facility_id'), item.get('username'), item.get('company'), item.get('practice'), e)
            self.logger.exception(msg)
            yield self.repeat_request(response, "MCNA parse members failed",
                                      'Received response: {}'.format(response.text))

    # Parse additional info for member and save to file
    def parse_member_info(self, response):
        try:
            info = json.loads(response.text)['get_member_info']
            item = response.meta['item'].copy()
            msg = 'Parsing additional info for member {} {} with facility ID {} for user "{}"' \
                .format(item['lname'], item['fname'], item['fid'], item['username'])
            self.logger.info(msg)
            item['address'] = '{} {}'.format(info['address1'], info['csz'])
            item['dob'] = info['dob']
            item['telephone'] = info['telephone']
            item['subscriber_id'] = info['subscriber_id']

            if self.scrape_mode == 'all':
                item['mco_sync_status'] = 'Updated (no PDF)'
                item['mco_status'] = True
                if self.tmhp:
                    yield self.tmhp.check_eligibility(item['subscriber_id'], item['dob'], item['fname'], item['lname'],
                                                      item.get('company'), item.get('practice'), self.name)
                yield item

            if self.scrape_mode == 'partial' or item.get('new_patient'):
                # Prepare data for fetching member eligibility info
                month, day, year = item['dob'].split('/')
                dob_formatted = "-".join([year, month, day])
                url = self.member_eligibility_url.format(item['mid'], item['subscriber_id'], dob_formatted, item['fid'])

                msg = 'Requesting eligibility info for member {} {} with facility ID {} for user "{}"' \
                    .format(item['lname'], item['fname'], item['fid'], item['username'])
                self.logger.info(msg)
                data = Request(url, callback=self.parse_member_eligibility, errback=self.error_handler)
                data.meta['item'] = item
                data.meta['cookiejar'] = response.meta['cookiejar']
                self.reset_counter()
                yield data
        except Exception as e:
            msg = 'Error occurred while parsing the additional info for member {} {} with facility ID {} for ' \
                  'user "{}", {} company and {} practice'.format(
                item['lname'], item['fname'], item['fid'], item['username'], item.get('company'), item.get('practice'))
            self.logger.exception(msg)
            self.logger.exception(e)
            yield self.repeat_request(response, "MCNA parse member failed",
                                      'Received response: {}'.format(response.text))

    def parse_member_eligibility(self, response):
        item = response.meta['item'].copy()
        try:
            if self.scrape_mode == 'all':
                msg = 'Parsing eligibility info for member {} {} with facility ID {} for user "{}"' \
                    .format(item['lname'], item['fname'], item['fid'], item['username'])
            else:
                msg = 'Parsing eligibility info for member with mid {} and facility ID {} for user "{}"' \
                    .format(item['mid'], item['fid'], item['username'])
            self.logger.info(msg)
            data = response.text
            soup = bs(data, 'lxml')
            plan = re.search(r'<div class="eligLabel">Plan:</div>(.*)</div>', data)
            item['mco_sync_status'] = 'Updated'
            if plan:
                item['plan'] = plan.group(1)
            else:
                item['plan'] = ''

            eg_date = re.search(r'This member is on the .* plan and became eligible for benefits on (\d+/\d+/\d+).',
                                data)
            active = 'active' in re.search(r'This member is currently .*', data).group(0)
            eligible = 'eligible' in re.search(r'Subscriber is .*', data).group(0)[:-5].lower()
            self.logger.info('active is: {} and eligible is {} for item: {}'.format(active, eligible, item))
            if active and eligible:
                item['mco_status'] = True
            else:
                item['mco_status'] = False

            if eg_date:
                item['became_eligible_on'] = eg_date.group(1)
            else:
                item['became_eligible_on'] = ''

            confirmation_no = re.search(r'Confirmation: (#\d+)<br/>', data)
            if confirmation_no:
                item['confirmation_no'] = confirmation_no.group(1)
            else:
                item['confirmation_no'] = ''

            services_table = soup.find_all('table', class_='services')[-1]
            service_date = services_table.find('td')
            if service_date:
                item['last_service_date'] = service_date.text
            else:
                item['last_service_date'] = ''

            last_prophylaxis_date = ''
            rows = []
            prophylaxis_child = soup.find('td', title='PROPHYLAXIS - CHILD')
            prophylaxis_adult = soup.find('td', title='PROPHYLAXIS - ADULT')

            # First check if adult prophy present as data is ordered chronologically in the html page
            if prophylaxis_adult:
                rows = prophylaxis_adult.findAllPrevious('tr')
            elif prophylaxis_child:
                rows = prophylaxis_child.findAllPrevious('tr')
            for row in rows:
                if row.find('td'):
                    if row.td.text != '':
                        last_prophylaxis_date = row.td.text
                        break
            item['last_prophylaxis_date'] = last_prophylaxis_date
            # Check if we have eligibility confirmation link in the page and if yes go to that link
            print_eligibility_link = soup.find('a', text='Print Eligibility Confirmation')
            if print_eligibility_link:
                url = self.base_url + print_eligibility_link['href']
                if self.scrape_mode == 'all':
                    msg = 'Requesting print eligibility info for member {} {} with facility ID {} for user "{}"' \
                        .format(item['lname'], item['fname'], item['fid'], item['username'])
                else:
                    msg = 'Requesting print eligibility info for member with mid {} and facility ID {} for user "{}"' \
                        .format(item['mid'], item['fid'], item['username'])
                self.logger.info(msg)
                data = Request(url, callback=self.parse_print_eligibility, errback=self.error_handler)
                data.meta['item'] = item
                data.meta['cookiejar'] = response.meta['cookiejar']
                self.reset_counter()
                yield data
            yield item
        except AttributeError:
            yield item
        except Exception as e:
            if self.scrape_mode == 'all':
                msg = 'Error occurred while parsing eligibility info for member {} {} with facility ID {} for user {}' \
                      ', {} company and {} practice'.format(
                    item['lname'], item['fname'], item['fid'], item['username'], item.get('company'), item.get('practice'))
            else:
                msg = 'Error occurred while parsing eligibility info for member with mid {} and facility ID {} for ' \
                      'user "{}", {} company and {} practice'.format(
                    item['mid'], item['fid'], item['username'], item.get('company'), item.get('practice'))
            self.logger.exception(msg)
            self.logger.exception(e)
            yield self.repeat_request(response, "MCNA parse member eligibility failed",
                                      'On host {} \n {}'.format(hostname, msg))

    def parse_print_eligibility(self, response):
        item = response.meta['item'].copy()
        if 'practice' in self.creds[0]:
            item['practice'] = self.creds[0].get('practice')
        item.pop('new_patient') if item.get('new_patient') else None

        if self.scrape_mode == 'all':
            msg = 'Rendering print eligibility info for member {} {} with facility ID {} for user "{}"' \
                .format(item['lname'], item['fname'], item['fid'], item['username'])
        else:
            msg = 'Rendering print eligibility info for member with mid {} and facility ID {} for user "{}"' \
                .format(item['mid'], item['fid'], item['username'])
        self.logger.info(msg)

        # If in partial scraping mode find patients name from the eligibility page data
        if self.scrape_mode == 'partial':
            soup = bs(response.text, 'lxml')
            item['fname'], item['lname'] = \
                soup.find('div', class_='infoLabel', text=("Subscriber's Name:")).parent.text.split(':')[
                    -1].strip().split(" ", 1)

        filename = '{} {}_{}_{}{}'.format(item['lname'], item['fname'], 'Eligibility', item['subscriber_id'], '.pdf')
        eligibility_dict = {'eligibility': 'requested', 'subscriber_id': item['subscriber_id'], 'jobid': item['jobid'],
                            'practice': item['practice'], 'mco_sync_status': 'Updated'}
        yield eligibility_dict
        if self.scrape_mode == 'partial':
            json_data = dict(html=response.text, collection='patient',
                             identifier={'subscriber_id': item['subscriber_id'], 'jobid': item['jobid']},
                             field='eligibility',
                             name=filename)
            response = self.zclient.send(json_data)
            if not (response and response['status'] == 'ok'):
                if self.scrape_mode == 'all':
                    msg = 'Error occurred during printing eligibility info for member {} {} with facility ID {} for user "{}"' \
                        .format(item['lname'], item['fname'], item['fid'], item['username'])
                else:
                    msg = 'Error occurred during printing eligibility info for member with mid {} and facility ID {} for user "{}"' \
                        .format(item['mid'], item['fid'], item['username'])
                self.logger.error(msg)

        # Check TMHP eligibility if patient is on medicaid plan
        if 'MEDICAID' in item['plan']:
            if self.tmhp:
                yield self.tmhp.check_eligibility(item['subscriber_id'], item['dob'], item['fname'], item['lname'],
                                                  item.get('company'), item.get('practice'), self.name)

