<?php
/**
 * Applies a title override at render time, whichever SEO plugin is active.
 *
 * Each SEO plugin builds the <title> through its own filter. The override is
 * hooked into all of them at a late priority so it wins, and into core for
 * sites without an SEO plugin. Pages without an override are left untouched.
 *
 * @package HoshyarSEO_Connector
 */

defined( 'ABSPATH' ) || exit;

/**
 * Title filter registration.
 */
class HoshyarSEO_Title_Filters {

	const PRIORITY = 999;

	/**
	 * Override storage.
	 *
	 * @var HoshyarSEO_Title_Store
	 */
	private $store;

	/**
	 * Constructor.
	 *
	 * @param HoshyarSEO_Title_Store $store Override storage.
	 */
	public function __construct( HoshyarSEO_Title_Store $store ) {
		$this->store = $store;
	}

	/**
	 * Hook into core and every supported SEO plugin.
	 *
	 * @return void
	 */
	public function register() {
		// Core returns this value unescaped, so it is escaped here. The SEO
		// plugins escape their own output, so they receive plain text.
		add_filter( 'pre_get_document_title', array( $this, 'core_title' ), self::PRIORITY );
		add_filter( 'wpseo_title', array( $this, 'plugin_title' ), self::PRIORITY );
		add_filter( 'rank_math/frontend/title', array( $this, 'plugin_title' ), self::PRIORITY );
		add_filter( 'aioseo_title', array( $this, 'plugin_title' ), self::PRIORITY );
		add_filter( 'seopress_titles_title', array( $this, 'plugin_title' ), self::PRIORITY );
	}

	/**
	 * Core document title.
	 *
	 * @param string $title Title so far.
	 * @return string
	 */
	public function core_title( $title ) {
		$override = $this->current_override();
		return null === $override ? $title : esc_html( $override );
	}

	/**
	 * SEO plugin title.
	 *
	 * @param string $title Title so far.
	 * @return string
	 */
	public function plugin_title( $title ) {
		$override = $this->current_override();
		return null === $override ? $title : $override;
	}

	/**
	 * Override for the page being rendered, if it is a single post of any type.
	 *
	 * @return string|null
	 */
	private function current_override() {
		if ( is_admin() || ! is_singular() ) {
			return null;
		}
		$post_id = get_queried_object_id();
		return $post_id ? $this->store->get( $post_id ) : null;
	}
}
