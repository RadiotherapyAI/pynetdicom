#!/usr/bin/env python
"""A QR Get SCU application.

For sending Query/Retrieve (QR) C-GET requests to a QR Get SCP.
"""

import argparse
import os
import sys

from pydicom.dataset import Dataset
from pydicom.uid import (
    ExplicitVRLittleEndian, ImplicitVRLittleEndian, ExplicitVRBigEndian
)

from pynetdicom import (
    AE, build_role, evt,
    StoragePresentationContexts,
    QueryRetrievePresentationContexts,
)
from pynetdicom.apps.common import setup_logging, create_dataset, handle_store
from pynetdicom._globals import DEFAULT_MAX_LENGTH
from pynetdicom.pdu_primitives import SOPClassExtendedNegotiation
from pynetdicom.sop_class import (
    PatientRootQueryRetrieveInformationModelGet,
    StudyRootQueryRetrieveInformationModelGet,
    PatientStudyOnlyQueryRetrieveInformationModelGet,
    EncapsulatedSTLStorage,
    EncapsulatedOBJStorage,
    EncapsulatedMTLStorage,
)


__version__ = '0.4.0'


def _setup_argparser():
    """Setup the command line arguments"""
    # Description
    parser = argparse.ArgumentParser(
        description=(
            "The getscu application implements a Service Class User "
            "(SCU) for the Query/Retrieve (QR) Service Class. getscu only "
            "supports query functionality using the C-GET message. It sends "
            "query keys to an SCP and waits for a response. The application "
            "can be used to test SCPs of the QR Service Class."
        ),
        usage="getscu [options] addr port"
    )

    # Parameters
    req_opts = parser.add_argument_group('Parameters')
    req_opts.add_argument(
        "addr", help="TCP/IP address or hostname of DICOM peer", type=str
    )
    req_opts.add_argument("port", help="TCP/IP port number of peer", type=int)

    # General Options
    gen_opts = parser.add_argument_group('General Options')
    gen_opts.add_argument(
        "--version",
        help="print version information and exit",
        action="store_true"
    )
    output = gen_opts.add_mutually_exclusive_group()
    output.add_argument(
        "-q", "--quiet",
        help="quiet mode, print no warnings and errors",
        action="store_const",
        dest='log_type', const='q'
    )
    output.add_argument(
        "-v", "--verbose",
        help="verbose mode, print processing details",
        action="store_const",
        dest='log_type', const='v'
    )
    output.add_argument(
        "-d", "--debug",
        help="debug mode, print debug information",
        action="store_const",
        dest='log_type', const='d'
    )
    gen_opts.add_argument(
        "-ll", "--log-level", metavar='[l]',
        help=(
            "use level l for the logger (critical, error, warn, info, debug)"
        ),
        type=str,
        choices=['critical', 'error', 'warn', 'info', 'debug']
    )
    parser.set_defaults(log_type='v')

    # Network Options
    net_opts = parser.add_argument_group('Network Options')
    net_opts.add_argument(
        "-aet", "--calling-aet", metavar='[a]etitle',
        help="set my calling AE title (default: GETSCU)",
        type=str,
        default='GETSCU'
    )
    net_opts.add_argument(
        "-aec", "--called-aet", metavar='[a]etitle',
        help="set called AE title of peer (default: ANY-SCP)",
        type=str,
        default='ANY-SCP'
    )
    net_opts.add_argument(
        "-ta", "--acse-timeout", metavar='[s]econds',
        help="timeout for ACSE messages (default: 30 s)",
        type=float,
        default=30
    )
    net_opts.add_argument(
        "-td", "--dimse-timeout", metavar='[s]econds',
        help="timeout for DIMSE messages (default: 30 s)",
        type=float,
        default=30
    )
    net_opts.add_argument(
        "-tn", "--network-timeout", metavar='[s]econds',
        help="timeout for the network (default: 30 s)",
        type=float,
        default=30
    )
    net_opts.add_argument(
        "-pdu", "--max-pdu", metavar='[n]umber of bytes',
        help=(
            f"set max receive pdu to n bytes (0 for unlimited, "
            f"default: {DEFAULT_MAX_LENGTH})"
        ),
        type=int,
        default=DEFAULT_MAX_LENGTH
    )

    # Query information model choices
    qr_group = parser.add_argument_group('Query Information Model Options')
    qr_model = qr_group.add_mutually_exclusive_group()
    qr_model.add_argument(
        "-P", "--patient",
        help="use patient root information model (default)",
        action="store_true"
    )
    qr_model.add_argument(
        "-S", "--study",
        help="use study root information model",
        action="store_true"
    )
    qr_model.add_argument(
        "-O", "--psonly",
        help="use patient/study only information model",
        action="store_true"
    )

    # Query Options
    qr_query = parser.add_argument_group('Query Options')
    qr_query.add_argument(
        '-k', '--keyword',
        metavar='[k]eyword: (gggg,eeee)=str, keyword=str',
        help=(
            "add or override a query element using either an element tag as "
            "(group,element) or the element's keyword (such as PatientName)"
        ),
        type=str,
        action='append',
    )
    qr_query.add_argument(
        '-f', '--file',
        metavar='path to [f]ile',
        help=(
            "use a DICOM file as the query dataset, if "
            "used with -k then the elements will be added to or overwrite "
            "those present in the file"
        ),
        type=str,
    )

    # Extended Negotiation Options
    ext_neg = parser.add_argument_group('Extended Negotiation Options')
    ext_neg.add_argument(
        '--relational-retrieval',
        help="request the use of relational retrieval",
        action="store_true",
    )
    ext_neg.add_argument(
        '--enhanced-conversion',
        help="request the use of enhanced multi-frame image conversion",
        action="store_true",
    )

    # Output Options
    out_opts = parser.add_argument_group('Output Options')
    out_opts.add_argument(
        '-od', "--output-directory", metavar="[d]irectory",
        help="write received objects to directory d",
        type=str
    )
    out_opts.add_argument(
        '--ignore',
        help="receive data but don't store it",
        action="store_true"
    )

    ns = parser.parse_args()
    if ns.version:
        pass
    elif not bool(ns.file) and not bool(ns.keyword):
        parser.error('-f and/or -k must be specified')

    return ns


def main(args=None):
    """Run the application."""
    if args is not None:
        sys.argv = args

    args = _setup_argparser()

    if args.version:
        print(f'getscu.py v{__version__}')
        sys.exit()

    APP_LOGGER = setup_logging(args, 'getscu')
    APP_LOGGER.debug(f'getscu.py v{__version__}')
    APP_LOGGER.debug('')

    # Create query (identifier) dataset
    try:
        # If you're looking at this to see how QR Get works then `identifer`
        # is a pydicom Dataset instance with your query keys, e.g.:
        #     identifier = Dataset()
        #     identifier.QueryRetrieveLevel = 'PATIENT'
        #     identifier.PatientName = '*'
        identifier = create_dataset(args, APP_LOGGER)
    except Exception as exc:
        APP_LOGGER.exception(exc)
        sys.exit(1)

    # Exclude these SOP Classes
    _exclusion = [
        EncapsulatedSTLStorage,
        EncapsulatedOBJStorage,
        EncapsulatedMTLStorage,
    ]
    store_contexts = [
        cx for cx in StoragePresentationContexts
        if cx.abstract_syntax not in _exclusion
    ]

    # Create application entity
    # Binding to port 0 lets the OS pick an available port
    ae = AE(ae_title=args.calling_aet)
    ae.acse_timeout = args.acse_timeout
    ae.dimse_timeout = args.dimse_timeout
    ae.network_timeout = args.network_timeout

    # Extended Negotiation - SCP/SCU Role Selection
    ext_neg = []
    ae.add_requested_context(PatientRootQueryRetrieveInformationModelGet)
    ae.add_requested_context(StudyRootQueryRetrieveInformationModelGet)
    ae.add_requested_context(PatientStudyOnlyQueryRetrieveInformationModelGet)
    for cx in store_contexts:
        ae.add_requested_context(cx.abstract_syntax)
        # Add SCP/SCU Role Selection Negotiation to the extended negotiation
        # We want to act as a Storage SCP
        ext_neg.append(build_role(cx.abstract_syntax, scp_role=True))

    if args.study:
        query_model = StudyRootQueryRetrieveInformationModelGet
    elif args.psonly:
        query_model = PatientStudyOnlyQueryRetrieveInformationModelGet
    else:
        query_model = PatientRootQueryRetrieveInformationModelGet

    # Extended Negotiation - SOP Class Extended
    ext_opts = [args.relational_retrieval, args.enhanced_conversion]
    if any(ext_opts):
        app_info = b''
        for option in ext_opts:
            app_info += b'\x01' if option else b'\x00'

        item = SOPClassExtendedNegotiation()
        item.sop_class_uid = query_model
        item.service_class_application_information = app_info
        ext_neg.append(item)

    # Request association with remote
    assoc = ae.associate(
        args.addr,
        args.port,
        ae_title=args.called_aet,
        ext_neg=ext_neg,
        evt_handlers=[(evt.EVT_C_STORE, handle_store, [args, APP_LOGGER])],
        max_pdu=args.max_pdu
    )

    if assoc.is_established:
        # Send query
        responses = assoc.send_c_get(identifier, query_model)
        for (status, rsp_identifier) in responses:
            # If `status.Status` is one of the 'Pending' statuses then
            #   `rsp_identifier` is the C-GET response's Identifier dataset
            if status and status.Status in [0xFF00, 0xFF01]:
                # `rsp_identifier` is a pydicom Dataset containing a query
                # response. You may want to do something interesting here...
                pass

        assoc.release()
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
