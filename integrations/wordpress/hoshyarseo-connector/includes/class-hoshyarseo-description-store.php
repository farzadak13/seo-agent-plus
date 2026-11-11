<?php
/**
 * Meta description overrides, kept and undone exactly as title overrides are.
 *
 * Its own meta keys: a description change and a title change on one page
 * have separate histories, and undoing one never touches the other.
 *
 * @package HoshyarSEO_Connector
 */

defined( 'ABSPATH' ) || exit;

/**
 * Stores, reads and reverts meta description overrides.
 */
class HoshyarSEO_Description_Store extends HoshyarSEO_Title_Store {

	const META_TITLE   = '_hoshyarseo_description';
	const META_HISTORY = '_hoshyarseo_description_history';
	const MAX_LENGTH   = 320;
	const LABEL        = 'meta description';
}
