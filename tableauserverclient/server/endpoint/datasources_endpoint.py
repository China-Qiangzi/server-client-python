from .endpoint import Endpoint
from .exceptions import MissingRequiredFieldError
from .fileuploads_endpoint import Fileuploads
from .. import RequestFactory, DatasourceItem, PaginationItem, ConnectionItem
import os
import logging
import copy
import cgi

# The maximum size of a file that can be published in a single request is 64MB
FILESIZE_LIMIT = 1024 * 1024 * 64   # 64MB

ALLOWED_FILE_EXTENSIONS = ['tds', 'tdsx', 'tde']

logger = logging.getLogger('tableau.endpoint.datasources')


class Datasources(Endpoint):
    def __init__(self, parent_srv):
        super(Endpoint, self).__init__()
        self.baseurl = "{0}/sites/{1}/datasources"
        self.parent_srv = parent_srv

    def _construct_url(self):
        return self.baseurl.format(self.parent_srv.baseurl, self.parent_srv.site_id)

    # Get all datasources
    def get(self, req_options=None):
        logger.info('Querying all datasources on site')
        url = self._construct_url()
        server_response = self.get_request(url, req_options)
        pagination_item = PaginationItem.from_response(server_response.content)
        all_datasource_items = DatasourceItem.from_response(server_response.content)
        return all_datasource_items, pagination_item

    # Get 1 datasource by id
    def get_by_id(self, datasource_id):
        if not datasource_id:
            error = "Datasource ID undefined."
            raise ValueError(error)
        logger.info('Querying single datasource (ID: {0})'.format(datasource_id))
        url = "{0}/{1}".format(self._construct_url(), datasource_id)
        server_response = self.get_request(url)
        return DatasourceItem.from_response(server_response.content)[0]

    # Populate datasource item's connections
    def populate_connections(self, datasource_item):
        if not datasource_item.id:
            error = 'Datasource item missing ID. Datasource must be retrieved from server first.'
            raise MissingRequiredFieldError(error)
        url = '{0}/{1}/connections'.format(self._construct_url(), datasource_item.id)
        server_response = self.get_request(url)
        datasource_item._set_connections(ConnectionItem.from_response(server_response.content))
        logger.info('Populated connections for datasource (ID: {0})'.format(datasource_item.id))

    # Delete 1 datasource by id
    def delete(self, datasource_id):
        if not datasource_id:
            error = "Datasource ID undefined."
            raise ValueError(error)
        url = "{0}/{1}".format(self._construct_url(), datasource_id)
        self.delete_request(url)
        logger.info('Deleted single datasource (ID: {0})'.format(datasource_id))

    # Download 1 datasource by id
    def download(self, datasource_id, filepath=None):
        if not datasource_id:
            error = "Datasource ID undefined."
            raise ValueError(error)
        url = "{0}/{1}/content".format(self._construct_url(), datasource_id)
        server_response = self.get_request(url)
        _, params = cgi.parse_header(server_response.headers['Content-Disposition'])
        filename = os.path.basename(params['filename'])
        if filepath is None:
            filepath = filename
        elif os.path.isdir(filepath):
            filepath = os.path.join(filepath, filename)

        with open(filepath, 'wb') as f:
            f.write(server_response.content)
        logger.info('Downloaded datasource to {0} (ID: {1})'.format(filepath, datasource_id))
        return os.path.abspath(filepath)

    # Update datasource
    def update(self, datasource_item):
        if not datasource_item.id:
            error = 'Datasource item missing ID. Datasource must be retrieved from server first.'
            raise MissingRequiredFieldError(error)
        url = "{0}/{1}".format(self._construct_url(), datasource_item.id)
        update_req = RequestFactory.Datasource.update_req(datasource_item)
        server_response = self.put_request(url, update_req)
        logger.info('Updated datasource item (ID: {0})'.format(datasource_item.id))
        updated_datasource = copy.copy(datasource_item)
        return updated_datasource._parse_common_tags(server_response.content)

    # Publish datasource
    def publish(self, datasource_item, file_path, mode):
        if not os.path.isfile(file_path):
            error = "File path does not lead to an existing file."
            raise IOError(error)
        if not mode or not hasattr(self.parent_srv.PublishMode, mode):
            error = 'Invalid mode defined.'
            raise ValueError(error)

        filename = os.path.basename(file_path)
        file_extension = os.path.splitext(filename)[1][1:]

        # If name is not defined, grab the name from the file to publish
        if not datasource_item.name:
            datasource_item.name = os.path.splitext(filename)[0]
        if file_extension not in ALLOWED_FILE_EXTENSIONS:
            error = "Only {} files can be published as datasources.".format(', '.join(ALLOWED_FILE_EXTENSIONS))
            raise ValueError(error)

        # Construct the url with the defined mode
        url = "{0}?datasourceType={1}".format(self._construct_url(), file_extension)
        if mode == self.parent_srv.PublishMode.Overwrite or mode == self.parent_srv.PublishMode.Append:
            url += '&{0}=true'.format(mode.lower())

        # Determine if chunking is required (64MB is the limit for single upload method)
        if os.path.getsize(file_path) >= FILESIZE_LIMIT:
            logger.info('Publishing {0} to server with chunking method (datasource over 64MB)'.format(filename))
            upload_session_id = Fileuploads.upload_chunks(self.parent_srv, file_path)
            url = "{0}&uploadSessionId={1}".format(url, upload_session_id)
            xml_request, content_type = RequestFactory.Datasource.publish_req_chunked(datasource_item)
        else:
            logger.info('Publishing {0} to server'.format(filename))
            with open(file_path, 'rb') as f:
                file_contents = f.read()
            xml_request, content_type = RequestFactory.Datasource.publish_req(datasource_item,
                                                                              filename,
                                                                              file_contents)
        server_response = self.post_request(url, xml_request, content_type)
        new_datasource = DatasourceItem.from_response(server_response.content)[0]
        logger.info('Published {0} (ID: {1})'.format(filename, new_datasource.id))
        return new_datasource